import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { providerAddressForDisplay, providerIdentityForDisplay, snapshotParameters } from '../src/utils/generationSnapshot.mjs'

const require = createRequire(import.meta.url)
const compiler = require('vue/compiler-sfc')
const sourceRoot = new URL('../src/', import.meta.url)
const template = async (path) => readFile(new URL(path, sourceRoot), 'utf8')

test('provider display never exposes URL credentials, path, query or fragment', () => {
  const raw = 'https://user:sk-secret@provider.example/private/sk-secret/v1?api_key=sk-secret#sk-secret'
  const visible = providerAddressForDisplay(raw)
  assert.equal(visible, 'https://provider.example/…')
  assert.doesNotMatch(visible, /sk-secret|user|private|api_key/)
  assert.equal(providerAddressForDisplay('not a URL containing sk-secret'), '地址已隐藏')
  assert.equal(providerAddressForDisplay('file:///private/sk-secret'), '地址已隐藏')
})

test('web snapshots show only a short provider fingerprint when URL is absent', () => {
  const hash = 'a'.repeat(64)
  assert.equal(providerIdentityForDisplay({ api_url_sha256: hash }), `SHA-256 ${'a'.repeat(12)}…`)
  assert.equal(providerIdentityForDisplay({ api_url_sha256: 'invalid-key-value' }), '未记录')
  assert.equal(providerIdentityForDisplay({ api_url: 'https://provider.example/v1?secret=1', api_url_sha256: hash }), 'https://provider.example/…')
  assert.equal(providerIdentityForDisplay(null), '未记录')
})

test('task snapshot parameters come from the saved run and remain in a stable order', () => {
  assert.deepEqual(snapshotParameters({ task: { parameters: { month: '2026-09', area: '华东' } } }), [
    { name: 'area', value: '华东' }, { name: 'month', value: '2026-09' },
  ])
  assert.deepEqual(snapshotParameters({ task: null }), [])
  assert.deepEqual(snapshotParameters(null), [])
})

test('snapshot component reads only its saved input and handles legacy null records', async () => {
  const snapshot = await template('components/analysis/GenerationSnapshot.vue')
  const { script } = compiler.parse({ source: snapshot })
  const code = script.content.replace(/^import .+$/gm, '').replace('export default', 'return')
  const component = new Function('providerIdentityForDisplay', 'snapshotParameters', code)(providerIdentityForDisplay, snapshotParameters)
  const old = { snapshot: null }
  assert.deepEqual(component.computed.history.call(old), [])
  assert.deepEqual(component.computed.parameters.call(old), [])
  const current = { snapshot: { history: [{ role: 'user', content: '当次问题' }], task: { parameters: { month: '2026-09' } } } }
  assert.deepEqual(component.computed.history.call(current), current.snapshot.history)
  assert.deepEqual(component.computed.parameters.call(current), [{ name: 'month', value: '2026-09' }])
  assert.match(snapshot, /<details class="generation-snapshot">/)
  assert.match(snapshot, /v-if="!snapshot"[^>]*>此历史记录未保存生成上下文/)
  assert.doesNotMatch(snapshot, /v-html/)
})
