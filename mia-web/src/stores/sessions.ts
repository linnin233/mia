import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  listSessions, createSession, renameSession,
  deleteSession, activateSession, getCurrentSession,
} from '@/api/sessions'
import type { SessionInfo } from '@/types'

export const useSessionStore = defineStore('sessions', () => {
  const sessions = ref<SessionInfo[]>([])
  const currentId = ref<string | null>(null)
  const loading = ref(false)

  const current = computed(() => sessions.value.find(s => s.session_id === currentId.value) || null)

  async function fetchSessions() {
    loading.value = true
    try {
      const data = await listSessions()
      sessions.value = data.sessions
      currentId.value = data.current_id
    } finally {
      loading.value = false
    }
  }

  async function create(name: string) {
    const s = await createSession(name)
    await fetchSessions()
    return s
  }

  async function rename(id: string, name: string) {
    await renameSession(id, name)
    await fetchSessions()
  }

  async function remove(id: string) {
    await deleteSession(id)
    await fetchSessions()
  }

  async function activate(id: string) {
    const s = await activateSession(id)
    currentId.value = s.session_id
    await fetchSessions()
  }

  return { sessions, currentId, current, loading, fetchSessions, create, rename, remove, activate }
})
