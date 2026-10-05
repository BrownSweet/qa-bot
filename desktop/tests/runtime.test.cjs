'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { PassThrough } = require('node:stream')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { BackendProcess, backendLaunch, clipboardHandler, desktopHeaders, isTrustedSender, isTrustedUrl, requestJson } = require('../runtime.cjs')

const origin = 'http://127.0.0.1:43123'
const token = 'test-transport-secret'

test('packaged launch uses bundled PyInstaller executables and ignores interpreter overrides', () => {
  for (const platform of ['darwin', 'win32']) {
    assert.deepEqual(backendLaunch({ packaged: true, platform, resourcesPath: '/bundle/resources',
      pythonOverride: '/untrusted/python', exists: () => false }), {
      command: `/bundle/resources/backend/qa-backend${platform === 'win32' ? '.exe' : ''}`, args: [],
    })
  }
})

test('Windows embedded runtime uses only fixed packaged Python and entry point paths', () => {
  const launch = backendLaunch({ packaged: true, platform: 'win32', resourcesPath: '/bundle/resources',
    pythonOverride: '/untrusted/python', exists: () => true,
    readFile: () => '{"runtime":"python-embedded","command":"evil.exe","args":["untrusted.py"]}' })
  assert.deepEqual(launch, { command: '/bundle/resources/backend/python.exe', args: ['/bundle/resources/backend/desktop_server.py'] })
  assert.deepEqual(backendLaunch({ packaged: true, platform: 'darwin', resourcesPath: '/bundle/resources',
    exists: () => true, readFile: () => '{"runtime":"python-embedded"}' }), {
    command: '/bundle/resources/backend/qa-backend', args: [],
  })
})

test('development launch honors the explicit interpreter but keeps a fixed repository entry point', () => {
  assert.deepEqual(backendLaunch({ packaged: false, platform: 'darwin', root: '/project',
    pythonOverride: '/venv/python', exists: () => false }), {
    command: '/venv/python', args: ['/project/backend/desktop_server.py'],
  })
})

function fakeChild() {
  const child = new EventEmitter()
  Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
    exitCode: null, signalCode: null, signals: [], kill(signal) { this.signals.push(signal) } })
  return child
}

function runtime(child, overrides = {}) {
  return new BackendProcess({ command: 'fake-backend', token, spawnFn: () => child,
    readyTimeoutMs: 1000, gracefulTimeoutMs: 5, killTimeoutMs: 5,
    requestFn: async () => ({ status: 'ready' }), ...overrides })
}

function exitChild(child, code = 0) {
  child.exitCode = code
  child.emit('exit', code, null)
}

test('startup waits for the complete ready line and authenticated health response', async () => {
  const child = fakeChild()
  let healthComplete, healthRequest
  const service = runtime(child, { requestFn: (...args) => {
    healthRequest = args
    return new Promise(resolve => { healthComplete = resolve })
  } })
  const started = service.start()
  child.stdout.write('loading data\nQA_DESKTOP_')
  assert.equal(service.origin, null)
  child.stdout.write('READY {"port":43123}\r\n')
  assert.deepEqual(healthRequest, [origin, '/health', token])
  assert.equal(service.origin, null)
  healthComplete({ status: 'ready' })
  assert.equal(await started, origin)
  exitChild(child)
})

test('invalid ready ports and malformed protocol JSON fail without contacting HTTP', async t => {
  for (const payload of ['{"port":0}', '{"port":65536}', '{"port":"43123"}', '{bad']) {
    await t.test(payload, async () => {
      const child = fakeChild()
      let requests = 0
      const service = runtime(child, { requestFn: async () => { requests++ } })
      const rejected = assert.rejects(service.start(), /无效/)
      child.stdout.write(`QA_DESKTOP_READY ${payload}\n`)
      await rejected
      assert.equal(requests, 0)
      exitChild(child)
    })
  }
})

test('startup health failure is not reported as a running backend', async () => {
  const child = fakeChild()
  const service = runtime(child, { requestFn: async () => { throw new Error('HTTP 403') } })
  const rejected = assert.rejects(service.start(), /403/)
  child.stdout.write('QA_DESKTOP_READY {"port":43123}\n')
  await rejected
  assert.equal(service.origin, null)
  exitChild(child)
})

test('a successful HTTP status without backend readiness still fails startup', async () => {
  const child = fakeChild()
  const service = runtime(child, { requestFn: async () => ({ status: 'starting' }) })
  const rejected = assert.rejects(service.start(), /尚未就绪/)
  child.stdout.write('QA_DESKTOP_READY {"port":43123}\n')
  await rejected
  assert.equal(service.origin, null)
  exitChild(child)
})

test('startup timeout and early process exit reject with a useful reason', async t => {
  await t.test('timeout', async () => {
    const child = fakeChild()
    const service = runtime(child, { readyTimeoutMs: 10 })
    await assert.rejects(service.start(), /启动超时/)
    exitChild(child)
  })
  await t.test('process exit', async () => {
    const child = fakeChild()
    const service = runtime(child)
    const rejected = assert.rejects(service.start(), /启动时退出.*7/)
    exitChild(child, 7)
    await rejected
  })
})

test('backend crash after readiness emits exactly one visible failure event', async () => {
  const child = fakeChild()
  const service = runtime(child)
  const failures = []
  service.on('unexpected-exit', detail => failures.push(detail))
  const started = service.start()
  child.stdout.write('QA_DESKTOP_READY {"port":43123}\n')
  await started
  exitChild(child, 3)
  assert.deepEqual(failures, [{ code: 3, signal: null }])
})

test('graceful shutdown sends stdin EOF once and does not signal an exited process', async () => {
  const child = fakeChild()
  const service = runtime(child)
  const started = service.start()
  child.stdout.write('QA_DESKTOP_READY {"port":43123}\n')
  await started
  let eofCount = 0
  child.stdin.on('finish', () => { eofCount++; exitChild(child) })
  const stopped = service.stop()
  assert.equal(service.stop(), stopped)
  await stopped
  assert.equal(eofCount, 1)
  assert.deepEqual(child.signals, [])
})

test('an unresponsive backend escalates termination and clears shutdown timers', async () => {
  const child = fakeChild()
  const service = runtime(child)
  const started = service.start()
  child.stdout.write('QA_DESKTOP_READY {"port":43123}\n')
  await started
  child.kill = signal => {
    child.signals.push(signal)
    if (signal === 'SIGKILL') exitChild(child, 1)
  }
  await service.stop()
  assert.deepEqual(child.signals, ['SIGTERM', 'SIGKILL'])
})

test('closing while startup is pending cancels readiness and shuts down the child', async () => {
  const child = fakeChild()
  const service = runtime(child)
  const rejected = assert.rejects(service.start(), /启动已取消/)
  child.stdin.on('finish', () => exitChild(child))
  await service.stop()
  await rejected
  assert.equal(service.origin, null)
})

test('IPC trusts only the live main frame in the main window at the exact origin', () => {
  const frame = { url: `${origin}/#/settings` }
  const contents = { mainFrame: frame }
  const window = { isDestroyed: () => false, webContents: contents }
  const event = { sender: contents, senderFrame: frame }
  assert.equal(isTrustedSender(event, window, origin), true)
  assert.equal(isTrustedSender({ ...event, senderFrame: { url: frame.url } }, window, origin), false)
  assert.equal(isTrustedSender({ ...event, sender: {} }, window, origin), false)
  frame.url = 'https://example.org'
  assert.equal(isTrustedSender(event, window, origin), false)
  frame.url = `${origin}/`
  assert.equal(isTrustedSender(event, { ...window, isDestroyed: () => true }, origin), false)
  assert.equal(isTrustedSender(event, null, origin), false)
})

test('navigation rejects lookalike origins, embedded credentials and executable URLs', () => {
  for (const url of ['http://localhost:43123/', 'http://127.0.0.1:43124/', 'http://127.0.0.1.evil:43123/',
    'http://name@127.0.0.1:43123/', 'file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hello']) {
    assert.equal(isTrustedUrl(url, origin), false, url)
  }
  assert.equal(isTrustedUrl(`${origin}/#/dashboard`, origin), true)
})

test('private headers are injected only into this window local API and health requests', () => {
  const details = { requestHeaders: { Accept: 'application/json', 'x-desktop-token': 'forged' },
    url: `${origin}/api/user/profile`, webContentsId: 10, frame: { url: `${origin}/` } }
  assert.deepEqual(desktopHeaders(details, origin, token, 10), { Accept: 'application/json', 'X-Desktop-Token': token })
  for (const overrides of [{ url: 'https://example.org/api/test' }, { url: `${origin}/api-evil` },
    { url: `${origin}/assets/app.js` }, { webContentsId: 11 }, { frame: { url: 'https://example.org' } }, { frame: null }]) {
    assert.deepEqual(desktopHeaders({ ...details, ...overrides }, origin, token, 10), { Accept: 'application/json' })
  }
  assert.equal(desktopHeaders({ ...details, url: `${origin}/health` }, origin, token, 10)['X-Desktop-Token'], token)
})

test('main-process JSON requests include the gate token and never follow redirects', async t => {
  let hits = 0
  const server = http.createServer((request, response) => {
    hits++
    assert.equal(request.headers['x-desktop-token'], token)
    if (request.url === '/redirect') {
      response.writeHead(302, { Location: '/health' }).end()
    } else {
      response.setHeader('Content-Type', 'application/json')
      response.end('{"status":"ok"}')
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => server.close(resolve)))
  const address = `http://127.0.0.1:${server.address().port}`
  assert.deepEqual(await requestJson(address, '/health', token), { status: 'ok' })
  await assert.rejects(requestJson(address, '/redirect', token), /HTTP 302/)
  assert.equal(hits, 2)
  await assert.rejects(requestJson('https://example.org', '/health', token), /无效/)
  await assert.rejects(requestJson(address, '//example.org', token), /无效/)
})

test('clipboard IPC authenticates callers before writing and caps UTF-8 text size', () => {
  const frame = { url: `${origin}/` }
  const contents = { mainFrame: frame }
  const window = { isDestroyed: () => false, webContents: contents }
  const event = { sender: contents, senderFrame: frame }
  const written = []
  const handler = clipboardHandler({
    assertSender: candidate => {
      if (!isTrustedSender(candidate, window, origin)) throw new Error('untrusted sender')
    },
    writeText: value => written.push(value),
  })
  assert.equal(handler(event, '固定合成复制文本'), true)
  assert.deepEqual(written, ['固定合成复制文本'])
  for (const value of [null, 123, {}, 'x'.repeat(2 * 1024 * 1024 + 1), '汉'.repeat(700000)]) {
    assert.throws(() => handler(event, value), /最多 2 MiB/)
  }
  assert.throws(() => handler({ ...event, senderFrame: { url: frame.url } }, 'rejected'), /untrusted/)
  frame.url = 'https://example.org/'
  assert.throws(() => handler(event, 'rejected'), /untrusted/)
  assert.deepEqual(written, ['固定合成复制文本'])
  frame.url = `${origin}/`
  assert.equal(handler(event, 'x'.repeat(2 * 1024 * 1024)), true)
  assert.equal(written[1].length, 2 * 1024 * 1024)
})

test('preload exposes only named desktop capabilities, without raw IPC or clipboard reading', async () => {
  let bridgeName, bridge
  const calls = []
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.cjs'), 'utf8'), {
    require: name => {
      assert.equal(name, 'electron')
      return {
        contextBridge: { exposeInMainWorld: (name, value) => { bridgeName = name; bridge = value } },
        ipcRenderer: { invoke: async (...args) => { calls.push(args); return 'result' } },
      }
    },
  })
  assert.equal(bridgeName, 'qaDesktop')
  assert.deepEqual(Object.keys(bridge).sort(), ['backup', 'bootstrap', 'copyText', 'exportDiagnostics', 'inspectBackup', 'openDataDirectory', 'openLogsDirectory', 'restartBackend', 'restoreBackup', 'selectFile'])
  await bridge.bootstrap()
  await bridge.selectFile('excel')
  await bridge.openDataDirectory()
  await bridge.copyText('固定合成复制文本')
  await bridge.backup('/must-not-forward-user-path')
  await bridge.inspectBackup()
  await bridge.restoreBackup()
  await bridge.exportDiagnostics()
  await bridge.restartBackend()
  await bridge.openLogsDirectory()
  assert.deepEqual(calls, [['qa:bootstrap'], ['qa:select-file', 'excel'], ['qa:open-data-directory'], ['qa:copy-text', '固定合成复制文本'], ['qa:backup'], ['qa:inspect-backup'], ['qa:restore-backup'], ['qa:export-diagnostics'], ['qa:restart-backend'], ['qa:open-logs-directory']])
  assert.equal(Object.isFrozen(bridge), true)
})
