import { defineStore } from 'pinia'
import { ref } from 'vue'
import { browseMemory, compactMemory } from '@/api/memory'
import type { MemoryEntry } from '@/types'

export const useMemoryStore = defineStore('memory', () => {
  const entries = ref<MemoryEntry[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)

  async function fetchEntries(p?: number) {
    if (p) page.value = p
    loading.value = true
    try {
      const data = await browseMemory(page.value, pageSize.value)
      entries.value = data.entries
      total.value = data.total
    } finally {
      loading.value = false
    }
  }

  async function compact() {
    await compactMemory()
    await fetchEntries(1)
  }

  return { entries, total, page, pageSize, loading, fetchEntries, compact }
})
