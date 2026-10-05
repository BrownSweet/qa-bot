const { spawnSync } = require('node:child_process')
const { mkdtempSync, rmSync } = require('node:fs')
const { join, resolve } = require('node:path')
const { tmpdir } = require('node:os')
const root = resolve(__dirname, '..')
const release = resolve(process.env.QA_RELEASE_DIR || join(root, 'release'))
const smokeArch = process.env.QA_SMOKE_ARCH || process.arch
if (!['arm64', 'x64'].includes(smokeArch)) throw new Error(`Unsupported smoke architecture: ${smokeArch}`)
const binary = process.platform === 'win32'
  ? join(release, 'win-unpacked', 'QA Robot.exe')
  : join(release, smokeArch === 'arm64' ? 'mac-arm64' : 'mac', 'QA Robot.app', 'Contents', 'MacOS', 'QA Robot')
const profile = mkdtempSync(join(tmpdir(), 'qa-robot-desktop-smoke-'))
let succeeded = false
try {
  const result = spawnSync(binary, [], {
    env: { ...process.env, QA_DESKTOP_SMOKE: '1', QA_DESKTOP_USER_DATA: profile },
    encoding: 'utf8', timeout: 90000, windowsHide: true,
  })
  process.stdout.write(result.stdout || '')
  process.stderr.write(result.stderr || '')
  if (result.error) throw result.error
  const report = (result.stdout || '').split('\n').map(line => {
    try { return JSON.parse(line) } catch { return null }
  }).find(value => value?.event === 'QA_DESKTOP_SMOKE')
  if (result.status !== 0 || report?.ok !== true) {
    throw new Error(`Packaged desktop smoke failed (${result.status})`)
  }
  succeeded = true
} finally {
  if (succeeded) rmSync(profile, { recursive: true, force: true })
  else process.stderr.write(`Smoke logs and screenshots preserved: ${profile}\n`)
}
