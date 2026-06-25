import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockGet = vi.fn()
const mockPut = vi.fn()

vi.mock('@/api/client', () => ({
  default: {
    get: (...args: any[]) => mockGet(...args),
    put: (...args: any[]) => mockPut(...args),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

import { getChannelStatus, toggleChannel } from '@/api/channels'

describe('Channel API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getChannelStatus returns channel info', async () => {
    mockGet.mockResolvedValue({
      data: {
        wechat: { enabled: true, has_token: true },
        telegram: { enabled: false, has_token: false },
      },
    })
    const result = await getChannelStatus()
    expect(result.wechat.enabled).toBe(true)
    expect(result.telegram.enabled).toBe(false)
    expect(mockGet).toHaveBeenCalledWith('/channels')
  })

  it('toggleChannel calls PUT with enabled flag', async () => {
    mockPut.mockResolvedValue({ data: {} })
    await toggleChannel('wechat', true)
    expect(mockPut).toHaveBeenCalledWith('/channels/wechat', { enabled: true })
  })

  it('toggleChannel disable calls PUT correctly', async () => {
    mockPut.mockResolvedValue({ data: {} })
    await toggleChannel('telegram', false)
    expect(mockPut).toHaveBeenCalledWith('/channels/telegram', { enabled: false })
  })
})
