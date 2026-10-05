import Vue from 'vue'
import ElementUI from 'element-ui'
import 'element-ui/lib/theme-chalk/index.css'

import App from './App.vue'
import router from './router'
import store from './store'
import { desktop } from './desktop'

Vue.use(ElementUI)
Vue.config.productionTip = false
Vue.config.errorHandler = (error, _vm, info) => {
  console.error(`[Vue ${info}] ${error.stack || error.message || error}`)
}

async function start() {
  try {
    if (desktop) {
      const session = await desktop.bootstrap()
      if (!session || !session.token || !session.user) throw new Error('本地服务未返回有效会话')
      store.commit('setAuth', { token: session.token, user: session.user })
      store.commit('setDesktop', { version: session.version, platform: session.platform })
    }
    new Vue({ router, store, render: (h) => h(App) }).$mount('#app')
  } catch (error) {
    const root = document.getElementById('app')
    const panel = document.createElement('div')
    panel.style.cssText = 'font:16px system-ui;padding:48px;max-width:640px;margin:auto;line-height:1.8'
    const title = document.createElement('h2')
    title.textContent = '问答机器人启动失败'
    const message = document.createElement('p')
    message.textContent = error.message || '无法连接本地服务，请重新启动应用。'
    const retry = document.createElement('button')
    retry.textContent = '重新连接'
    retry.addEventListener('click', () => window.location.reload())
    panel.append(title, message, retry)
    root.replaceChildren(panel)
  }
}

start()
