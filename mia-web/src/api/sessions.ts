import client from './client'
import type { SessionInfo } from '@/types'

export async function listSessions(): Promise<{ sessions: SessionInfo[]; current_id: string | null }> {
  const { data } = await client.get('/sessions')
  return data
}

export async function createSession(name: string): Promise<SessionInfo> {
  const { data } = await client.post('/sessions', { name })
  return data
}

export async function renameSession(sessionId: string, name: string): Promise<void> {
  await client.put(`/sessions/${sessionId}`, { name })
}

export async function deleteSession(sessionId: string): Promise<void> {
  await client.delete(`/sessions/${sessionId}`)
}

export async function activateSession(sessionId: string): Promise<SessionInfo> {
  const { data } = await client.post(`/sessions/${sessionId}/activate`)
  return data
}

export async function getCurrentSession(): Promise<SessionInfo> {
  const { data } = await client.get('/sessions/current')
  return data
}

export async function getSessionHistory(sessionId: string): Promise<{ session_id: string; messages: { role: string; content: string }[] }> {
  const { data } = await client.get(`/sessions/${sessionId}/history`)
  return data
}
