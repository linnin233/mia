import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 180000 })

export async function fetchSessions() {
  const { data } = await api.get('/sessions')
  return data
}

export async function activateSession(id: string) {
  const { data } = await api.post(`/sessions/${id}/activate`)
  return data
}

export async function createSession(name: string) {
  const { data } = await api.post('/sessions', { name })
  return data
}

export async function renameSession(id: string, name: string) {
  await api.put(`/sessions/${id}`, { name })
}

export async function deleteSession(id: string) {
  await api.delete(`/sessions/${id}`)
}

export async function fetchSessionHistory(id: string) {
  const { data } = await api.get(`/sessions/${id}/history`)
  return data
}

export async function fetchChannels() {
  const { data } = await api.get('/channels')
  return data
}

export async function toggleChannel(name: string, enabled: boolean) {
  await api.put(`/channels/${name}`, { enabled })
}

export async function fetchConfig() {
  const { data } = await api.get('/config')
  return data
}

export async function fetchModels() {
  const { data } = await api.get('/models')
  return data
}

export async function toggleModel(id: string, enabled: boolean) {
  await api.put(`/models/${id}`, { enabled })
}

export async function updateAgent(name: string, config: Record<string, any>) {
  await api.put(`/agents/${name}`, config)
}

export async function fetchMemory(page = 1, size = 20) {
  const { data } = await api.get('/memory', { params: { page, page_size: size } })
  return data
}

export async function compactMemory() {
  await api.post('/compact')
}

export async function sendMessage(query: string, sessionId?: string) {
  const { data } = await api.post('/chat', { query, session_id: sessionId || '' })
  return data
}

export async function fetchInterfaceDetail(name: string) {
  const { data } = await api.get(`/interface/${name}`)
  return data
}

export async function updateInterfaceToken(name: string, token: string) {
  await api.put(`/interface/${name}/token`, { token })
}

export async function getQrCode() {
  const { data } = await api.post('/interface/wechat/qrcode')
  return data
}

export async function pollQrCode(qrcode: string) {
  const { data } = await api.get(`/interface/wechat/qrcode/${qrcode}`)
  return data
}

export default api
