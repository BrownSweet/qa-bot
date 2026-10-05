import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'

test('production build shares one Vue runtime with Element UI dynamic messages', async () => {
  const result = await build({
    root: fileURLToPath(new URL('../', import.meta.url)),
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    logLevel: 'silent',
    build: { write: false, minify: false },
  })
  const outputs = (Array.isArray(result) ? result : [result]).flatMap((item) => item.output)
  const runtimeModules = new Set(outputs.filter((item) => item.type === 'chunk')
    .flatMap((chunk) => Object.keys(chunk.modules))
    .filter((id) => /\/vue\/dist\/vue\.runtime\.(esm|common(?:\.prod)?)\.js$/.test(id)))
  assert.equal(runtimeModules.size, 1, `Multiple Vue runtimes break Element UI messages: ${[...runtimeModules].join(', ')}`)
  assert.ok([...runtimeModules][0].endsWith('/vue.runtime.esm.js'))
})
