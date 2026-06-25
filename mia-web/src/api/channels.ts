import client from './client'
import type { ChannelsResponse } from '@/types'

export interface InterfaceDetail {
  name: string
  has_token: boolean
  enabled: boolean
  login_method: string
  token_file: string
  token_masked: string
  file_size: number
  file_mtime: string
  base_url: string
  ctx_file?: string
  ctx_user_count?: number
  ctx_file_size?: number
  ctx_file_mtime?: string
}

export async function getChannelStatus(): Promise<ChannelsResponse> {
  const { data } = await client.get('/channels')
  return data
}

export async function toggleChannel(name: string, enabled: boolean): Promise<void> {
  await client.put(`/channels/${name}`, { enabled })
}

export async function getInterfaceDetail(name: string): Promise<InterfaceDetail> {
  const { data } = await client.get(`/interface/${name}`)
  return data
}

export async function updateInterfaceToken(name: string, token: string): Promise<void> {
  await client.put(`/interface/${name}/token`, { token })
}
