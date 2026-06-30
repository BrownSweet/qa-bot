import Vue from 'vue'
import Vuex from 'vuex'

Vue.use(Vuex)

const TOKEN_KEY = 'qabot_token'
const USER_KEY = 'qabot_user'

const store = new Vuex.Store({
  state: {
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: JSON.parse(localStorage.getItem(USER_KEY) || 'null'),
  },
  mutations: {
    setAuth(state, { token, user }) {
      state.token = token
      state.user = user
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.setItem(USER_KEY, JSON.stringify(user))
    },
    setUser(state, user) {
      state.user = user
      localStorage.setItem(USER_KEY, JSON.stringify(user))
    },
    clearAuth(state) {
      state.token = ''
      state.user = null
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
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
