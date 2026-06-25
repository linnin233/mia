import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/chat', () => ({
  sendMessage: vi.fn(),
}))

import { useChatStore } from '@/stores/chat'
import * as chatApi from '@/api/chat'

describe('Chat Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state has empty messages', () => {
    const store = useChatStore()
    expect(store.messages).toHaveLength(0)
    expect(store.loading).toBe(false)
  })

  it('addMessage appends to list', () => {
    const store = useChatStore()
    store.addMessage('user', 'Hello')
    store.addMessage('assistant', 'Hi there')
    expect(store.messages).toHaveLength(2)
    expect(store.messages[0].role).toBe('user')
    expect(store.messages[1].content).toBe('Hi there')
  })

  it('send adds user message and response', async () => {
    vi.mocked(chatApi.sendMessage).mockResolvedValue({ response: 'Hello back' })
    const store = useChatStore()
    await store.send('Hi')
    expect(store.messages).toHaveLength(2)
    expect(store.messages[0].role).toBe('user')
    expect(store.messages[1].role).toBe('assistant')
    expect(store.messages[1].content).toBe('Hello back')
  })

  it('send handles empty query', async () => {
    const store = useChatStore()
    await store.send('')
    expect(store.messages).toHaveLength(0)
  })

  it('send handles API error gracefully', async () => {
    vi.mocked(chatApi.sendMessage).mockRejectedValue(new Error('Network error'))
    const store = useChatStore()
    await store.send('Hello')
    expect(store.messages).toHaveLength(2)
    expect(store.messages[1].content).toContain('Error')
  })

  it('clearMessages resets list', () => {
    const store = useChatStore()
    store.addMessage('user', 'Test')
    store.clearMessages()
    expect(store.messages).toHaveLength(0)
  })
})
