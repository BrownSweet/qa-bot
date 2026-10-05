'use strict'
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const { spawnSync } = require('node:child_process')
const root = path.resolve(__dirname, '..')

function buildConfig(target, arch, signed, env = process.env, base = require('../package.json').build) {
  if (!['mac', 'win'].includes(target) || !['x64', 'arm64'].includes(arch) || (target === 'win' && arch !== 'x64')) throw new Error('Unsupported build target')
  const buildRoot = path.resolve(env.QA_BUILD_ROOT || path.join(root, 'build'))
  const config = { ...base, beforePack: path.join(root, 'scripts/verify_backend.cjs'),
    directories: { ...base.directories, output: path.resolve(env.QA_RELEASE_DIR || path.join(root, 'release')) },
    mac: { ...base.mac, extraResources: [{ from: path.join(buildRoot, 'backend/qa-backend'), to: 'backend' }] },
    win: { ...base.win, extraResources: [{ from: path.join(buildRoot, 'backend-windows/qa-backend'), to: 'backend' }] } }
  if (signed) {
    config.forceCodeSigning = true
    if (target === 'mac') {
      if (!env.QA_MAC_SIGN_IDENTITY || env.QA_MAC_SIGN_IDENTITY === '-' || !env.CSC_LINK || !env.CSC_KEY_PASSWORD ||
          !env.APPLE_ID || !env.APPLE_APP_SPECIFIC_PASSWORD || !env.APPLE_TEAM_ID) {
        throw new Error('Signed Mac release requires QA_MAC_SIGN_IDENTITY, CSC_LINK, CSC_KEY_PASSWORD, APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD and APPLE_TEAM_ID; no unsigned fallback is allowed')
      }
      config.mac = { ...config.mac, identity: env.QA_MAC_SIGN_IDENTITY, hardenedRuntime: true, notarize: true }
    } else {
      if (!(env.WIN_CSC_LINK || env.CSC_LINK) || !(env.WIN_CSC_KEY_PASSWORD || env.CSC_KEY_PASSWORD)) throw new Error('Signed Windows release requires a certificate and password; no unsigned fallback is allowed')
      config.win = { ...config.win, signExecutable: true, signAndEditExecutable: true, forceCodeSigning: true }
    }
  }
  return config
}

if (require.main === module) {
  const [target, arch] = process.argv.slice(2)
  const config = buildConfig(target, arch, process.argv.includes('--signed'))
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-builder-config-'))
  try {
    const file = path.join(temporary, 'electron-builder.json')
    fs.writeFileSync(file, JSON.stringify(config), { mode: 0o600 })
    const result = spawnSync(process.execPath, [require.resolve('electron-builder/out/cli/cli.js'), `--${target}`, `--${arch}`, '--publish', 'never', '--config', file], { cwd: root, stdio: 'inherit' })
    if (result.error) throw result.error
    process.exitCode = result.status ?? 1
  } finally { fs.rmSync(temporary, { recursive: true, force: true }) }
}
module.exports = { buildConfig }
