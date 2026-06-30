import axios from 'axios'
import { Message } from 'element-ui'
import router from '../router'
import store from '../store'

const http = axios.create({ baseURL: import.meta.env.VITE_API_URL || '/api', timeout: 60000 })

http.interceptors.request.use((config) => {
  const token = store.state.token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const data = error.response && error.response.data
    const msg = (data && data.message) || error.message || '请求失败'
    if (error.response && error.response.status === 401) {
      store.dispatch('logout')
      if (router.currentRoute.path !== '/auth') router.replace('/auth')
    }
    Message.error(msg)
    return Promise.reject(new Error(msg))
  }
)

// ===== 认证 =====
export const login = (data) => http.post('/auth/login', data)
export const register = (data) => http.post('/auth/register', data)
export const sendCode = (data) => http.post('/auth/send-code', data)
export const forgotPassword = (data) => http.post('/auth/forgot-password', data)

// ===== 数据库配置 =====
export const getDbConfigs = () => http.get('/db/configs')
export const addDbConfig = (data) => http.post('/db/configs', data)
export const deleteDbConfig = (id) => http.delete(`/db/configs/${id}`)
export const testConnection = (data) => http.post('/db/test-connection', data)

// ===== 会话 =====
export const getSessions = (keyword) => http.get('/sessions', { params: { keyword } })
export const createSession = (data) => http.post('/sessions', data)
export const updateSession = (id, data) => http.put(`/sessions/${id}`, data)
export const deleteSession = (id) => http.delete(`/sessions/${id}`)

// ===== 问答 =====
export const getMessages = (sessionId) => http.get(`/chat/messages/${sessionId}`)

// ===== 系统配置 =====
export const getSystemConfig = () => http.get('/system/config')
export const updateSystemConfig = (data) => http.put('/system/config', data)
export const testAi = () => http.post('/system/test-ai')

// ===== 用户 =====
export const getProfile = () => http.get('/user/profile')
export const updateProfile = (data) => http.put('/user/profile', data)
export const changePassword = (data) => http.post('/user/change-password', data)

// ===== 通知 =====
export const getNotifications = (params) => http.get('/notifications', { params })
export const updateNotification = (id, data) => http.put(`/notifications/${id}`, data)
export const deleteNotification = (id) => http.delete(`/notifications/${id}`)
export const readAllNotifications = () => http.post('/notifications/read-all')

// ===== 导出（文件下载）=====
export async function exportSession(sessionId, format) {
  const resp = await fetch(`${import.meta.env.VITE_API_URL || '/api'}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${store.state.token}` },
    body: JSON.stringify({ session_id: sessionId, format }),
  })
  if (!resp.ok) throw new Error('导出失败')
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = format === 'csv' ? 'session_export.csv' : 'session_export.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * SSE 流式问答。通过 fetch 读取流，回调 onStatus / onMessage / onComplete。
 * 返回 AbortController，用于“停止”。
 */
export function chatStream(payload, { onStatus, onMessage, onComplete, onError }) {
  const controller = new AbortController()
  fetch(`${import.meta.env.VITE_API_URL || '/api'}/chat/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${store.state.token}` },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.message || '请求失败')
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() // 保留不完整的最后一块
        for (const block of blocks) {
          handleBlock(block, { onStatus, onMessage, onComplete })
        }
      }
    })
    .catch((e) => {
      if (e.name === 'AbortError') return
      onError && onError(e)
    })
  return controller
}

function handleBlock(block, { onStatus, onMessage, onComplete }) {
  let event = 'message'
  let dataStr = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
  }
  if (!dataStr) return
  let data
  try { data = JSON.parse(dataStr) } catch (e) { return }
  if (event === 'status') onStatus && onStatus(data)
  else if (event === 'message') onMessage && onMessage(data)
  else if (event === 'complete') onComplete && onComplete(data)
}

export default http
