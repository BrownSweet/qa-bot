import axios from 'axios'
import { Message } from 'element-ui'
import router from '../router'
import store from '../store'
import { isDesktop } from '../desktop'
import { consumeChatStream } from '../utils/chat.mjs'

const apiBase = isDesktop ? '/api' : (import.meta.env.VITE_API_URL || '/api')
const http = axios.create({ baseURL: apiBase, timeout: 60000 })

function authFailure(message) {
  if (isDesktop) return '本地会话已失效，请重新启动应用'
  store.dispatch('logout')
  if (router.currentRoute.path !== '/auth') router.replace('/auth')
  return message
}

http.interceptors.request.use((config) => {
  const token = store.state.token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const data = error.response && error.response.data
    let msg = (data && data.message) || error.message || '请求失败'
    if (error.response && error.response.status === 401) {
      msg = authFailure(msg)
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
export const updateDbConfig = (id, data) => http.put(`/db/configs/${id}`, data)
export const testAndUpdateDbConfig = (id, data) => http.post(`/db/configs/${id}/test-update`, data, { timeout: 45000 })
export const deleteDbConfig = (id) => http.delete(`/db/configs/${id}`)
export const testConnection = (data) => http.post('/db/test-connection', data)
export const testSavedConnection = (id) => http.post(`/db/configs/${id}/test-connection`)
export const getDbSchema = (id) => http.get(`/db/configs/${id}/schema`)
export const getDbPreview = (id, table) => http.get(`/db/configs/${id}/preview`, { params: { table } })
export const getDbSemantics = (id) => http.get(`/db/configs/${id}/semantics`)
export const updateDbSemantics = (id, data) => http.put(`/db/configs/${id}/semantics`, data)

// ===== 会话 =====
export const getSessions = (keyword) => http.get('/sessions', { params: { keyword } })
export const createSession = (data) => http.post('/sessions', data)
export const updateSession = (id, data) => http.put(`/sessions/${id}`, data)
export const deleteSession = (id) => http.delete(`/sessions/${id}`)

// ===== 问答 =====
export const getMessages = (sessionId) => http.get(`/chat/messages/${sessionId}`)

// ===== 系统配置 =====
export const getSystemConfig = () => http.get('/system/config')
export const getReadiness = () => http.get('/system/readiness')
export const updateSystemConfig = (data) => http.put('/system/config', data)
export const testAi = () => http.post('/system/test-ai')

export const getAnalysisTasks = () => http.get('/analysis-tasks')
export const createAnalysisTask = (data) => http.post('/analysis-tasks', data)
export const updateAnalysisTask = (id, data) => http.put(`/analysis-tasks/${id}`, data)
export const deleteAnalysisTask = (id) => http.delete(`/analysis-tasks/${id}`)
export const prepareAnalysisTask = (id, data) => http.post(`/analysis-tasks/${id}/prepare`, data)
export const getAnalysisTaskRuns = (id) => http.get(`/analysis-tasks/${id}/runs`)
export const getEvaluationCases = () => http.get('/evaluation/cases')
export const importEvaluationCases = (data) => http.post('/evaluation/cases/import', data, { timeout: 300000 })
export const createEvaluationCase = (data) => http.post('/evaluation/cases', data, { timeout: 180000 })
export const updateEvaluationCase = (id, data) => http.put(`/evaluation/cases/${id}`, data, { timeout: 180000 })
export const deleteEvaluationCase = (id) => http.delete(`/evaluation/cases/${id}`)
export const runEvaluationCase = (id) => http.post(`/evaluation/cases/${id}/run`, {}, { timeout: 180000 })
export const getEvaluationRuns = (id) => http.get(`/evaluation/cases/${id}/runs`)

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
  return downloadExport('/export', { session_id: sessionId, format }, 'session_export')
}

export async function exportResult(messageId, format) {
  return downloadExport('/export-result', { message_id: messageId, format }, 'query_result_preview')
}

async function downloadExport(path, payload, name) {
  const resp = await fetch(`${apiBase}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${store.state.token}` },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) await throwResponseError(resp, '导出失败')
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}.${payload.format === 'csv' ? 'csv' : 'xlsx'}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/**
 * SSE 流式问答。通过 fetch 读取流，回调 onStatus / onMessage / onComplete。
 * 返回 AbortController，用于“停止”。
 */
export function chatStream(payload, { onStatus, onMessage, onResult, onComplete, onError }) {
  const controller = new AbortController()
  fetch(`${apiBase}/chat/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${store.state.token}` },
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok) await throwResponseError(resp, '请求失败')
      await consumeChatStream(resp.body, { onStatus, onMessage, onResult, onComplete }, controller.signal)
    })
    .catch((e) => {
      if (e.name === 'AbortError') return
      onError && onError(e)
    })
  return controller
}

async function throwResponseError(resp, fallback) {
  const data = await resp.json().catch(() => ({}))
  const message = data.message || fallback
  throw new Error(resp.status === 401 ? authFailure(message) : message)
}

export default http
