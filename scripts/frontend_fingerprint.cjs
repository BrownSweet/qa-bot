'use strict'

const { createHash } = require('node:crypto')
const { existsSync, lstatSync, readFileSync, readdirSync } = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const frontend = path.join(root, 'frontend')
const manifestName = 'qa-build-info.json'

function walk(relative, files) {
  const absolute = path.join(root, relative)
  if (!existsSync(absolute)) return
  const status = lstatSync(absolute)
  if (status.isSymbolicLink()) throw new Error(`Frontend build input cannot be a symlink: ${relative}`)
  if (status.isDirectory()) {
    for (const name of readdirSync(absolute).sort()) walk(path.join(relative, name), files)
  } else if (status.isFile()) files.push(relative.split(path.sep).join('/'))
}

function fingerprint(mode = 'production', env = process.env) {
  const files = []
  for (const name of ['src', 'public', 'index.html', 'package.json', 'package-lock.json', 'vite.config.js',
    '.env', '.env.local', `.env.${mode}`, `.env.${mode}.local`]) walk(path.join('frontend', name), files)
  walk(path.join('scripts', 'frontend_fingerprint.cjs'), files)
  const hash = createHash('sha256')
  hash.update(JSON.stringify({ mode, environment: Object.fromEntries(
    Object.entries(env).filter(([key]) => key.startsWith('VITE_')).sort(([a], [b]) => a.localeCompare(b))
  ) }))
  for (const name of files.sort()) {
    hash.update('\0' + name + '\0')
    hash.update(createHash('sha256').update(readFileSync(path.join(root, name))).digest('hex'))
  }
  return hash.digest('hex')
}

function assertFrontendBuild() {
  const manifestPath = path.join(frontend, 'dist', manifestName)
  if (!existsSync(manifestPath)) throw new Error('Frontend build provenance is missing; run npm run frontend:build')
  let info
  try { info = JSON.parse(readFileSync(manifestPath, 'utf8')) }
  catch { throw new Error('Frontend build provenance is invalid; rebuild the frontend') }
  if (info.mode !== 'production' || info.fingerprint !== fingerprint('production')) {
    throw new Error('Frontend source or build environment changed after the build; run npm run frontend:build')
  }
  if (!existsSync(path.join(frontend, 'dist', 'index.html'))) throw new Error('Frontend build is incomplete')
}

if (require.main === module) {
  assertFrontendBuild()
  process.stdout.write('Frontend build matches the current source and build environment\n')
}

module.exports = { assertFrontendBuild, fingerprint, manifestName }
