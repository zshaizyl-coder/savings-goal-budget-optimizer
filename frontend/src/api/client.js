import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1', timeout: 30000 })

api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(response => response.data, error => {
  const payload = error.response?.data?.error
  const mapped = new Error(payload?.message || '请求失败，请稍后重试。')
  mapped.code = payload?.code || 'NETWORK_ERROR'
  mapped.details = payload?.details || {}
  return Promise.reject(mapped)
})

export function saveToken(token) { localStorage.setItem('access_token', token) }
export function clearToken() { localStorage.removeItem('access_token') }
