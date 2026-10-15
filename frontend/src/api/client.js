import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1', timeout: 30000 })

api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export function mapApiError(error) {
  if (error.code === 'ECONNABORTED') {
    const timeout = new Error('账单仍在后台处理中，请稍后刷新导入历史，不要重复上传。')
    timeout.code = 'REQUEST_TIMEOUT'
    timeout.details = {}
    return timeout
  }
  const payload = error.response?.data?.error
  const mapped = new Error(payload?.message || '请求失败，请稍后重试。')
  mapped.code = payload?.code || 'NETWORK_ERROR'
  mapped.details = payload?.details || {}
  return mapped
}

export function shouldResetSession(error) { return error.response?.status === 401 }

api.interceptors.response.use(response => response.data, error => {
  if (shouldResetSession(error)) {
    clearToken()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') window.location.assign('/login')
  }
  return Promise.reject(mapApiError(error))
})

export function saveToken(token) { localStorage.setItem('access_token', token) }
export function clearToken() { localStorage.removeItem('access_token') }
