import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getChannelStatus, toggleChannel } from '@/api/channels'
import type { ChannelsResponse } from '@/types'

export const useChannelStore = defineStore('channels', () => {
  const channels = ref<ChannelsResponse>({
    wechat: { enabled: false, has_token: false },
    telegram: { enabled: false, has_token: false },
  })
  const loading = ref(false)

  async function fetchStatus() {
    loading.value = true
    try {
      channels.value = await getChannelStatus()
    } finally {
      loading.value = false
    }
  }

  async function toggle(name: string, enabled: boolean) {
    await toggleChannel(name, enabled)
    await fetchStatus()
  }

  return { channels, loading, fetchStatus, toggle }
})
