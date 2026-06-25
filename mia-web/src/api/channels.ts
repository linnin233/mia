import client from './client'
import type { ChannelsResponse } from '@/types'

export async function getChannelStatus(): Promise<ChannelsResponse> {
  const { data } = await client.get('/channels')
  return data
}

export async function toggleChannel(name: string, enabled: boolean): Promise<void> {
  await client.put(`/channels/${name}`, { enabled })
}
