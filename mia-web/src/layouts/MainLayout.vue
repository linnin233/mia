<template>
  <el-container style="height: 100vh">
    <el-header height="56px" style="background: #1a1a2e; color: #eee; display: flex; align-items: center; padding: 0 20px">
      <span style="font-size: 18px; font-weight: bold; margin-right: 30px">MIA Web</span>
      <el-select
        v-model="selectedSession"
        placeholder="Select session"
        size="small"
        style="width: 220px"
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
        <el-menu-item index="/chat">Chat</el-menu-item>
        <el-menu-item index="/sessions">Sessions</el-menu-item>
        <el-menu-item index="/memory">Memory</el-menu-item>
        <el-menu-item index="/settings">Settings</el-menu-item>
      </el-menu>
    </el-header>
    <el-container>
      <el-aside width="220px" style="background: #f5f7fa; border-right: 1px solid #e4e7ed; padding: 16px">
        <AppSidebar />
      </el-aside>
      <el-main style="padding: 0; background: #fff">
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

const route = useRoute()
const sessionStore = useSessionStore()
const channelStore = useChannelStore()

const selectedSession = ref('')

onMounted(async () => {
  await sessionStore.fetchSessions()
  await channelStore.fetchStatus()
  selectedSession.value = sessionStore.currentId || ''
})

watch(() => sessionStore.currentId, (id) => {
  if (id) selectedSession.value = id
})

async function switchSession(id: string) {
  if (id && id !== sessionStore.currentId) {
    await sessionStore.activate(id)
  }
}
</script>
