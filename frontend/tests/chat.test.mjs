import test from 'node:test'
import assert from 'node:assert/strict'
import { consumeChatStream, questionForReply, shouldSendOnEnter } from '../src/utils/chat.mjs'

const encoder = new TextEncoder()
function streamOf(text, chunkSize = 1) {
  const bytes = encoder.encode(text)
  return new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += chunkSize) controller.enqueue(bytes.slice(i, i + chunkSize))
      controller.close()
    },
  })
}

test('SSE handles split UTF-8, CRLF, heartbeats and one completion', async () => {
  const events = []
  await consumeChatStream(streamOf(
    ': heartbeat\r\n\r\nevent: status\r\ndata: {"status":"analyzing"}\r\n\r\n' +
    'event: message\r\ndata: {"content":"销售额：￥100"}\r\n\r\n' +
    'event: complete\r\ndata: {"message_id":"answer-1","status":"completed"}\r\n\r\n' +
    'event: complete\r\ndata: {"message_id":"answer-1","status":"completed"}\r\n\r\n'
  ), {
    onStatus: (data) => events.push(['status', data.status]),
    onMessage: (data) => events.push(['message', data.content]),
    onComplete: (data) => events.push(['complete', data.message_id]),
  })
  assert.deepEqual(events, [['status', 'analyzing'], ['message', '销售额：￥100'], ['complete', 'answer-1']])
})

test('SSE premature EOF rejects instead of leaving the UI generating', async () => {
  const contents = []
  await assert.rejects(
    consumeChatStream(streamOf('event: message\ndata: {"content":"部分内容"}\n\n'), { onMessage: (data) => contents.push(data.content) }),
    /回答连接已中断/
  )
  assert.deepEqual(contents, ['部分内容'])
})

test('SSE completion at EOF is processed without requiring a trailing blank line', async () => {
  let completed = false
  await consumeChatStream(streamOf('event: complete\ndata: {"status":"cancelled"}'), {
    onComplete: (data) => { completed = data.status === 'cancelled' },
  })
  assert.equal(completed, true)
})

test('SSE malformed events fail visibly', async () => {
  await assert.rejects(consumeChatStream(streamOf('event: message\ndata: {invalid}\n\n'), {}), /无法解析/)
})

test('SSE forwards saved result evidence separately from assistant text', async () => {
  const evidence = { sql: 'SELECT 1 LIMIT 1000', columns: ['value'], rows: [[1]], coverage: { returned_rows: 1 } }
  const result = { message_id: 'a1', source: { id: 'db1', name: '本地' }, evidence }
  let received
  await consumeChatStream(streamOf(`event: result\ndata: ${JSON.stringify(result)}\n\nevent: complete\ndata: {"message_id":"a1"}\n\n`), {
    onResult(value) { received = value },
  })
  assert.deepEqual(received, result)
})

test('explicit question link takes precedence over neighboring user messages', () => {
  assert.equal(questionForReply([
    { id: 'u1', role: 'user', content: '原问题' }, { id: 'u2', role: 'user', content: '另一个问题' },
    { id: 'a1', role: 'assistant', question_message_id: 'u1' },
  ], 'a1'), '原问题')
})

test('SSE abort ignores queued content and releases the reader', async () => {
  const controller = new AbortController()
  controller.abort()
  let called = false
  const body = streamOf('event: message\ndata: {"content":"旧答案"}\n\n')
  await consumeChatStream(body, { onMessage: () => { called = true } }, controller.signal)
  assert.equal(called, false)
  assert.equal(body.locked, false)
})

test('retry resolves the selected answer instead of the newest question', () => {
  const messages = [
    { id: 'u1', role: 'user', content: '第一个问题' }, { id: 'a1', role: 'assistant', content: '第一答' },
    { id: 'u2', role: 'user', content: '第二个问题' }, { id: 'a2', role: 'assistant', content: '第二答' },
  ]
  assert.equal(questionForReply(messages, 'a1'), '第一个问题')
  assert.equal(questionForReply(messages, 'a2'), '第二个问题')
  assert.equal(questionForReply(messages, 'missing'), '')
})

test('Enter preserves IME composition and Shift+Enter', () => {
  assert.equal(shouldSendOnEnter({ isComposing: true }), false)
  assert.equal(shouldSendOnEnter({ keyCode: 229 }), false)
  assert.equal(shouldSendOnEnter({ shiftKey: true }), false)
  assert.equal(shouldSendOnEnter({ isComposing: false, shiftKey: false, keyCode: 13 }), true)
})
