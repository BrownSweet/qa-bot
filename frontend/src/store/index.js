import Vue from 'vue'
import Vuex from 'vuex'
import { isDesktop } from '../desktop'

Vue.use(Vuex)

const TOKEN_KEY = 'qabot_token'
const USER_KEY = 'qabot_user'

function savedAuth() {
  if (isDesktop) return { token: '', user: null }
  try {
    return { token: localStorage.getItem(TOKEN_KEY) || '', user: JSON.parse(localStorage.getItem(USER_KEY) || 'null') }
  } catch {
    return { token: '', user: null }
  }
}

const store = new Vuex.Store({
  state: {
    ...savedAuth(),
    desktop: null,
    workspace: { sessionId: '', dbConfigId: '' },
  },
  mutations: {
    setAuth(state, { token, user }) {
      state.token = token
      state.user = user
      if (!isDesktop) {
        localStorage.setItem(TOKEN_KEY, token)
        localStorage.setItem(USER_KEY, JSON.stringify(user))
      }
    },
    setUser(state, user) {
      state.user = user
      if (!isDesktop) localStorage.setItem(USER_KEY, JSON.stringify(user))
    },
    setDesktop(state, info) { state.desktop = info },
    setWorkspace(state, selection) { state.workspace = selection },
    clearAuth(state) {
      state.token = ''
      state.user = null
      state.workspace = { sessionId: '', dbConfigId: '' }
      if (!isDesktop) {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(USER_KEY)
      }
    },
  },
  actions: {
    loginSuccess({ commit }, payload) {
      commit('setAuth', payload)
    },
    logout({ commit }) {
      commit('clearAuth')
    },
  },
  getters: {
    isAuthenticated: (state) => !!state.token,
  },
})

export default store
