import client from './client'
import type { ChatResponse } from '@/types'

export async function sendMessage(query: string, image?: string, voice?: string): Promise<ChatResponse> {
  const { data } = await client.post('/chat', { query, image, voice })
  return data
}

export function streamChat(query: string, image?: string, voice?: string): EventSource {
  const params = new URLSearchParams()
  // Use fetch + ReadableStream for POST SSE
  return {} as EventSource
}
