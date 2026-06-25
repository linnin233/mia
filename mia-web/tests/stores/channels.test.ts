import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/channels', () => ({
  getChannelStatus: vi.fn(),
  toggleChannel: vi.fn(),
}))

import { useChannelStore } from '@/stores/channels'
import * as api from '@/api/channels'

describe('Channel Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial channels are disabled', () => {
    const store = useChannelStore()
    expect(store.channels.wechat.enabled).toBe(false)
    expect(store.channels.telegram.enabled).toBe(false)
  })

  it('fetchStatus updates channel state', async () => {
    vi.mocked(api.getChannelStatus).mockResolvedValue({
      wechat: { enabled: true, has_token: true },
      telegram: { enabled: true, has_token: false },
    })
    const store = useChannelStore()
    await store.fetchStatus()
    expect(store.channels.wechat.enabled).toBe(true)
    expect(store.channels.telegram.has_token).toBe(false)
  })

  it('toggle calls API and refreshes', async () => {
    vi.mocked(api.getChannelStatus).mockResolvedValue({
      wechat: { enabled: false, has_token: true },
      telegram: { enabled: false, has_token: false },
    })
    const store = useChannelStore()
    await store.toggle('wechat', false)
    expect(api.toggleChannel).toHaveBeenCalledWith('wechat', false)
    expect(store.channels.wechat.enabled).toBe(false)
  })
})
