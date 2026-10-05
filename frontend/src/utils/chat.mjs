export function questionForReply(messages, replyId) {
  const index = messages.findIndex((message) => message.id === replyId)
  if (index < 0) return ''
  const parentId = messages[index].question_message_id
  if (parentId) {
    const parent = messages.find((message) => message.id === parentId && message.role === 'user')
    if (parent) return parent.content
  }
  for (let i = index - 1; i >= 0; i--) {
    if (messages[i].role === 'user') return messages[i].content
  }
  return ''
}

export function shouldSendOnEnter(event) {
  return !event.shiftKey && !event.isComposing && event.keyCode !== 229
}

function parseEvent(block) {
  let event = 'message'
  const lines = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) lines.push(line.slice(5).replace(/^ /, ''))
  }
  if (!lines.length) return null
  try {
    return { event, data: JSON.parse(lines.join('\n')) }
  } catch {
    throw new Error('收到无法解析的回答，请重试')
  }
}

export async function consumeChatStream(body, callbacks, signal) {
  if (!body) throw new Error('服务未返回回答流')
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  const dispatch = (block) => {
    if (completed || signal?.aborted) return
    const parsed = parseEvent(block)
    if (!parsed) return
    const { event, data } = parsed
    if (event === 'status') callbacks.onStatus?.(data)
    else if (event === 'message') callbacks.onMessage?.(data)
    else if (event === 'result') callbacks.onResult?.(data)
    else if (event === 'complete') {
      completed = true
      callbacks.onComplete?.(data)
    }
  }
  try {
    while (!completed) {
      const { value, done } = await reader.read()
      if (signal?.aborted) return
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        dispatch(buffer.slice(0, boundary.index))
        buffer = buffer.slice(boundary.index + boundary[0].length)
      }
      if (done) {
        if (buffer.trim()) dispatch(buffer)
        break
      }
    }
    if (!completed && !signal?.aborted) throw new Error('回答连接已中断，请重试')
  } finally {
    // complete 是协议终点；取消剩余传输，释放读取锁。
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
