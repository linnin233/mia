import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/memory', () => ({
  browseMemory: vi.fn(),
  compactMemory: vi.fn(),
}))

import { useMemoryStore } from '@/stores/memory'
import * as api from '@/api/memory'

describe('Memory Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state is empty', () => {
    const store = useMemoryStore()
    expect(store.entries).toHaveLength(0)
    expect(store.total).toBe(0)
    expect(store.page).toBe(1)
  })

  it('fetchEntries populates data', async () => {
    vi.mocked(api.browseMemory).mockResolvedValue({
      total: 42,
      page: 1,
      page_size: 20,
      entries: [
        { id: '1', content: 'Test', category: 'fact', confidence: 0.8, keywords: [], importance: 0.5, source_sessions: [], created_at: '' },
      ],
    })
    const store = useMemoryStore()
    await store.fetchEntries(1)
    expect(store.total).toBe(42)
    expect(store.entries).toHaveLength(1)
  })
})
