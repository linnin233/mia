<template>
  <el-container style="height: 100vh; overflow: hidden">
    <el-header height="48px" style="background: #1a1a2e; color: #eee; display: flex; align-items: center; padding: 0 16px; flex-shrink: 0">
      <span style="font-size: 16px; font-weight: bold; margin-right: 20px; white-space: nowrap">MIA 控制台</span>
      <el-select
        v-model="selectedSession"
        placeholder="选择会话"
        size="small"
        style="max-width: 220px"
        @change="switchSession"
      >
        <el-option
          v-for="s in sessionStore.sessions"
          :key="s.session_id"
          :label="`${s.name} (${s.turn_count})`"
          :value="s.session_id"
        />
      </el-select>
      <div style="flex: 1" />
      <el-menu
        mode="horizontal"
        :default-active="route.path"
        router
        style="background: transparent; border: none; --el-menu-text-color: #ccc; --el-menu-hover-text-color: #fff; --el-menu-active-color: #409EFF"
      >
        <el-menu-item index="/chat">聊天</el-menu-item>
        <el-menu-item index="/sessions">会话</el-menu-item>
        <el-menu-item index="/memory">记忆</el-menu-item>
        <el-menu-item index="/settings">设置</el-menu-item>
      </el-menu>
    </el-header>
    <el-container style="flex: 1; overflow: hidden">
      <el-aside style="background: #f5f7fa; border-right: 1px solid #e4e7ed; padding: 12px; overflow-y: auto; flex-shrink: 0">
        <AppSidebar />
      </el-aside>
      <el-main style="padding: 0; background: #fff; overflow-y: auto; flex: 1">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/sessions'
import { useChannelStore } from '@/stores/channels'
import AppSidebar from '@/components/layout/AppSidebar.vue'

import { useChatStore } from '@/stores/chat'

const route = useRoute()
const sessionStore = useSessionStore()
const channelStore = useChannelStore()
const chatStore = useChatStore()

const selectedSession = ref('')

onMounted(async () => {
  await sessionStore.fetchSessions()
  await channelStore.fetchStatus()
  selectedSession.value = sessionStore.currentId || ''
  if (selectedSession.value) {
    await chatStore.loadHistory(selectedSession.value)
  }
})

watch(() => sessionStore.currentId, (id) => {
  if (id) selectedSession.value = id
})

async function switchSession(id: string) {
  if (id && id !== sessionStore.currentId) {
    await sessionStore.activate(id)
    await chatStore.loadHistory(id)
  }
}
</script>
