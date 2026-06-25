import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/sessions', () => ({
  listSessions: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  activateSession: vi.fn(),
  getCurrentSession: vi.fn(),
}))

import { useSessionStore } from '@/stores/sessions'
import * as api from '@/api/sessions'

describe('Session Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state is empty', () => {
    const store = useSessionStore()
    expect(store.sessions).toHaveLength(0)
    expect(store.currentId).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('fetchSessions populates list', async () => {
    vi.mocked(api.listSessions).mockResolvedValue({
      sessions: [
        { session_id: '1', name: 'A', source: 'cli', turn_count: 3, created_at: '', updated_at: '', is_active: true },
        { session_id: '2', name: 'B', source: 'cli', turn_count: 1, created_at: '', updated_at: '', is_active: false },
      ],
      current_id: '1',
    })
    const store = useSessionStore()
    await store.fetchSessions()
    expect(store.sessions).toHaveLength(2)
    expect(store.currentId).toBe('1')
  })

  it('current computed returns active session', async () => {
    vi.mocked(api.listSessions).mockResolvedValue({
      sessions: [
        { session_id: '1', name: 'Active', source: 'cli', turn_count: 5, created_at: '', updated_at: '', is_active: true },
      ],
      current_id: '1',
    })
    const store = useSessionStore()
    await store.fetchSessions()
    expect(store.current?.name).toBe('Active')
  })
})
