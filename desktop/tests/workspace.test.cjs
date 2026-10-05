const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { EventEmitter } = require('node:events')
const { PassThrough } = require('node:stream')
const { RotatingLog, workspaceCommand } = require('../runtime.cjs')
const { buildConfig } = require('../../scripts/package_desktop.cjs')

test('logs rotate with a bounded retention count', t => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-log-test-'))
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }))
  const file = path.join(folder, 'desktop.log')
  const log = new RotatingLog(file, { maxBytes: 32, count: 3 })
  for (let index = 0; index < 12; index++) log.write('synthetic-line\n')
  assert.equal(fs.readdirSync(folder).length, 3)
  for (const name of fs.readdirSync(folder)) assert.ok(fs.statSync(path.join(folder, name)).size <= 32)
})

test('workspace command preserves paths with spaces as argv and verifies structured result', async () => {
  let spawnArgs
  const child = new EventEmitter()
  child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.kill = () => {}
  const result = workspaceCommand({ command: '/fixed/python', args: ['/fixed/desktop_server.py'] },
    { operation: 'backup', file: '/safe folder/backup.zip', dataDir: '/private data', env: {}, includeSources: true },
    { spawnFn: (...args) => { spawnArgs = args; return child } })
  child.stdout.write('QA_WORKSPACE_RESULT {"valid":true}\n')
  child.emit('close', 0)
  assert.deepEqual(await result, { valid: true })
  assert.deepEqual(spawnArgs[1], ['/fixed/desktop_server.py', '--workspace', 'backup', '--file', '/safe folder/backup.zip', '--data-dir', '/private data', '--include-sources'])
})

test('workspace CLI errors reject instead of presenting successful backup', async () => {
  const child = new EventEmitter()
  child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.kill = () => {}
  const result = workspaceCommand({ command: 'fixed', args: [] }, { operation: 'inspect', file: 'broken.zip', dataDir: 'data' }, { spawnFn: () => child })
  const rejected = assert.rejects(result, /密钥不匹配/)
  child.stdout.write('QA_WORKSPACE_RESULT {"error":"密钥不匹配"}\n')
  child.emit('close', 1)
  await rejected
})

test('signed packaging refuses missing credentials instead of silently making unsigned releases', () => {
  assert.throws(() => buildConfig('mac', 'arm64', true, {}), /no unsigned fallback/)
  assert.throws(() => buildConfig('win', 'x64', true, {}), /no unsigned fallback/)
  assert.equal(buildConfig('win', 'x64', false, {}).win.signExecutable, false)
  assert.notEqual(buildConfig('win', 'x64', false, {}).win.signAndEditExecutable, false)
  const signedWindows = buildConfig('win', 'x64', true, { WIN_CSC_LINK: 'fixture', WIN_CSC_KEY_PASSWORD: 'fixture' })
  assert.equal(signedWindows.win.signExecutable, true)
  const config = buildConfig('mac', 'arm64', true, { QA_MAC_SIGN_IDENTITY: 'Developer ID Application: Test', CSC_LINK: 'fixture', CSC_KEY_PASSWORD: 'fixture', APPLE_ID: 'fixture', APPLE_APP_SPECIFIC_PASSWORD: 'fixture', APPLE_TEAM_ID: 'fixture', QA_RELEASE_DIR: '/tmp/isolated-release' })
  assert.equal(config.forceCodeSigning, true)
  assert.equal(config.mac.notarize, true)
  assert.equal(config.directories.output, '/tmp/isolated-release')
  assert.equal(JSON.stringify(config).includes('CSC_KEY_PASSWORD'), false)
})
