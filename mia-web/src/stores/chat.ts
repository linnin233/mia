import { defineStore } from 'pinia'
import { ref } from 'vue'
import { sendMessage } from '@/api/chat'
import { getSessionHistory } from '@/api/sessions'
import type { ChatMessage } from '@/types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const loading = ref(false)
  const streamingText = ref('')

  function addMessage(role: 'user' | 'assistant', content: string) {
    messages.value.push({
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
      role,
      content,
      timestamp: Date.now(),
    })
  }

  async function send(query: string) {
    if (!query.trim()) return
    addMessage('user', query)
    loading.value = true
    try {
      const res = await sendMessage(query)
      addMessage('assistant', res.response)
    } catch (e: any) {
      addMessage('assistant', `Error: ${e.message || 'Request failed'}`)
    } finally {
      loading.value = false
    }
  }

  async function loadHistory(sessionId: string) {
    messages.value = []
    try {
      const data = await getSessionHistory(sessionId)
      for (const m of data.messages) {
        messages.value.push({
          id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8) + messages.value.length,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: Date.now(),
        })
      }
    } catch {}
  }

  function clearMessages() {
    messages.value = []
  }

  return { messages, loading, streamingText, addMessage, send, loadHistory, clearMessages }
})
