import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

vi.mock('@/api/client', () => ({
  default: {
    get: (...args: any[]) => mockGet(...args),
    post: (...args: any[]) => mockPost(...args),
    put: (...args: any[]) => mockPut(...args),
    delete: (...args: any[]) => mockDelete(...args),
  },
}))

import { listSessions, createSession, renameSession, deleteSession, activateSession } from '@/api/sessions'

describe('Session API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('listSessions returns sessions array', async () => {
    mockGet.mockResolvedValue({
      data: { sessions: [{ session_id: '1', name: 'Test', source: 'cli', turn_count: 0 }], current_id: '1' },
    })
    const result = await listSessions()
    expect(result.sessions).toHaveLength(1)
    expect(result.current_id).toBe('1')
    expect(mockGet).toHaveBeenCalledWith('/sessions')
  })

  it('createSession calls POST with name', async () => {
    mockPost.mockResolvedValue({
      data: { session_id: 'new', name: 'NewSession', source: 'cli', turn_count: 0 },
    })
    const result = await createSession('NewSession')
    expect(result.name).toBe('NewSession')
    expect(mockPost).toHaveBeenCalledWith('/sessions', { name: 'NewSession' })
  })

  it('renameSession calls PUT with session id', async () => {
    mockPut.mockResolvedValue({ data: {} })
    await renameSession('abc123', 'Renamed')
    expect(mockPut).toHaveBeenCalledWith('/sessions/abc123', { name: 'Renamed' })
  })

  it('deleteSession calls DELETE with session id', async () => {
    mockDelete.mockResolvedValue({ data: {} })
    await deleteSession('abc123')
    expect(mockDelete).toHaveBeenCalledWith('/sessions/abc123')
  })

  it('activateSession calls POST with session id', async () => {
    mockPost.mockResolvedValue({
      data: { session_id: 'abc123', name: 'Test', source: 'cli', turn_count: 5 },
    })
    const result = await activateSession('abc123')
    expect(result.session_id).toBe('abc123')
    expect(mockPost).toHaveBeenCalledWith('/sessions/abc123/activate')
  })
})
