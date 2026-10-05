const { readFileSync, existsSync, readdirSync } = require('node:fs')
const { resolve, join, relative, sep } = require('node:path')
const { createHash } = require('node:crypto')
const { assertFrontendBuild } = require('./frontend_fingerprint.cjs')

module.exports = async function verifyBackend(context) {
  const target = context.electronPlatformName
  const folder = target === 'win32' ? 'backend-windows' : 'backend'
  const buildRoot = process.env.QA_BUILD_ROOT || resolve(__dirname, '..', 'build')
  const directory = resolve(buildRoot, folder, 'qa-backend')
  const info = JSON.parse(readFileSync(join(directory, 'build-info.json'), 'utf8'))
  const expected = ({ 0: 'ia32', 1: 'x64', 3: 'arm64' })[context.arch]
  const actual = ({ AMD64: 'x64', x86_64: 'x64', aarch64: 'arm64' })[info.architecture] || info.architecture
  if (info.platform !== target || actual !== expected) {
    throw new Error(`Backend platform mismatch: ${info.platform}/${actual}; required ${target}/${expected}. Rebuild the backend for this target.`)
  }
  const executable = info.runtime === 'python-embedded' ? 'python.exe' : target === 'win32' ? 'qa-backend.exe' : 'qa-backend'
  if (!existsSync(join(directory, executable))) throw new Error('Packaged backend executable is missing')
  if (!info.source_sha256 || !Object.keys(info.source_sha256).length) throw new Error('Backend provenance is missing; rebuild with the current build script')
  const sourceRoot = resolve(__dirname, '..', 'backend')
  const appSources = folder => readdirSync(folder, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
    ? appSources(join(folder, entry.name)) : entry.isFile() && entry.name.endsWith('.py') ? [join(folder, entry.name)] : [])
  const sourceNames = ['main.py', 'desktop_server.py', ...appSources(join(sourceRoot, 'app')).map(file => relative(sourceRoot, file).split(sep).join('/'))].sort()
  if (JSON.stringify(sourceNames) !== JSON.stringify(Object.keys(info.source_sha256).sort())) throw new Error('Backend source file list changed after build; rebuild before packaging')
  for (const [file, expectedHash] of Object.entries(info.source_sha256)) {
    const actualHash = createHash('sha256').update(readFileSync(resolve(__dirname, '..', 'backend', file))).digest('hex')
    if (actualHash !== expectedHash) throw new Error(`Backend source changed after build: ${file}. Rebuild the backend before packaging.`)
  }
  const requirements = createHash('sha256').update(readFileSync(resolve(__dirname, '..', 'backend/requirements-desktop.lock'))).digest('hex')
  if (requirements !== info.requirements_sha256) throw new Error('Backend dependency lock changed after build; rebuild before packaging')
  assertFrontendBuild()
}
