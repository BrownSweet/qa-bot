import Vue from 'vue'
import VueRouter from 'vue-router'
import store from '../store'

Vue.use(VueRouter)

const routes = [
  { path: '/auth', component: () => import('../views/AuthView.vue'), meta: { guest: true } },
  { path: '/', component: () => import('../views/DashboardView.vue'), meta: { auth: true } },
  { path: '/settings', component: () => import('../views/SettingsView.vue'), meta: { auth: true } },
  { path: '*', redirect: '/' },
]

const router = new VueRouter({ mode: 'history', routes })

router.beforeEach((to, from, next) => {
  const authed = !!store.state.token
  if (to.meta.auth && !authed) return next('/auth')
  if (to.meta.guest && authed) return next('/')
  next()
})

export default router
