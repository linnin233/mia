import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockGet = vi.fn()
const mockPost = vi.fn()

vi.mock('@/api/client', () => ({
  default: {
    get: (...args: any[]) => mockGet(...args),
    post: (...args: any[]) => mockPost(...args),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import { browseMemory, compactMemory } from '@/api/memory'

describe('Memory API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('browseMemory returns paginated entries', async () => {
    mockGet.mockResolvedValue({
      data: { total: 50, page: 1, page_size: 20, entries: [] },
    })
    const result = await browseMemory(1, 20)
    expect(result.total).toBe(50)
    expect(mockGet).toHaveBeenCalledWith('/memory', { params: { page: 1, page_size: 20 } })
  })

  it('browseMemory defaults to page 1', async () => {
    mockGet.mockResolvedValue({
      data: { total: 0, page: 1, page_size: 20, entries: [] },
    })
    await browseMemory()
    expect(mockGet).toHaveBeenCalledWith('/memory', { params: { page: 1, page_size: 20 } })
  })

  it('compactMemory calls POST', async () => {
    mockPost.mockResolvedValue({
      data: { ok: true, before: 100, after: 50 },
    })
    const result = await compactMemory()
    expect(result.ok).toBe(true)
    expect(mockPost).toHaveBeenCalledWith('/compact')
  })
})
