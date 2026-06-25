import client from './client'
import type { MemoryResponse } from '@/types'

export async function browseMemory(page = 1, pageSize = 20): Promise<MemoryResponse> {
  const { data } = await client.get('/memory', { params: { page, page_size: pageSize } })
  return data
}

export async function compactMemory(): Promise<{ ok: boolean; before: number; after: number }> {
  const { data } = await client.post('/compact')
  return data
}
