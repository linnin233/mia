<template>
  <div>
    <h4 style="margin: 0 0 12px; font-size: 14px; color: #606266">Channels</h4>
    <div style="margin-bottom: 8px" v-for="ch in channelList" :key="ch.key">
      <el-tag :type="ch.enabled ? 'success' : 'info'" size="small" style="margin-right: 6px">
        {{ ch.enabled ? 'ON' : 'OFF' }}
      </el-tag>
      <span style="font-size: 13px">{{ ch.label }}</span>
    </div>
    <el-divider style="margin: 12px 0" />
    <h4 style="margin: 0 0 8px; font-size: 14px; color: #606266">Sessions</h4>
    <div
      v-for="s in sessionStore.sessions"
      :key="s.session_id"
      @click="switchTo(s.session_id)"
      :style="{
        padding: '6px 8px',
        marginBottom: '4px',
        borderRadius: '4px',
        cursor: 'pointer',
        fontSize: '13px',
        background: s.session_id === sessionStore.currentId ? '#ecf5ff' : 'transparent',
        color: s.session_id === sessionStore.currentId ? '#409EFF' : '#303133',
      }"
    >
      <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
        {{ s.name }}
      </span>
      <span style="font-size: 11px; color: #909399">
        {{ sourceLabel(s.source) }} | {{ s.turn_count }} turns
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useSessionStore } from '@/stores/sessions'
import { useChannelStore } from '@/stores/channels'
import { useChatStore } from '@/stores/chat'

const sessionStore = useSessionStore()
const channelStore = useChannelStore()
const chatStore = useChatStore()

const channelList = computed(() => [
  { key: 'wechat', label: 'WeChat', enabled: channelStore.channels.wechat?.enabled },
  { key: 'telegram', label: 'Telegram', enabled: channelStore.channels.telegram?.enabled },
])

function sourceLabel(source: string): string {
  const map: Record<string, string> = { cli: 'CLI', wechat: 'WeChat', telegram: 'TG', api: 'API' }
  return map[source] || source
}

async function switchTo(id: string) {
  if (id !== sessionStore.currentId) {
    await sessionStore.activate(id)
    await chatStore.loadHistory(id)
  }
}
</script>
