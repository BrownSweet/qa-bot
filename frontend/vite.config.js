import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue2'
import frontendBuild from '../scripts/frontend_fingerprint.cjs'

function buildProvenance(mode) {
  let initialFingerprint
  return {
    name: 'qa-build-provenance',
    apply: 'build',
    buildStart() { initialFingerprint = frontendBuild.fingerprint(mode) },
    generateBundle() {
      if (initialFingerprint !== frontendBuild.fingerprint(mode)) {
        throw new Error('Frontend source changed during build; restart the build')
      }
      this.emitFile({ type: 'asset', fileName: frontendBuild.manifestName,
        source: JSON.stringify({ mode, fingerprint: initialFingerprint }) + '\n' })
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [vue(), buildProvenance(mode)],
  // Register before Vite creates its resolvers: Vue 2's plugin adds this alias
  // too late for CommonJS imports in Vite 7, giving Element UI a second runtime.
  resolve: {
    alias: { vue: 'vue/dist/vue.runtime.esm.js' },
    dedupe: ['vue'],
  },
  server: {
    host: '127.0.0.1',
    port: 5555,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
}))
