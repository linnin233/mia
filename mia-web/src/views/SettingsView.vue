<template>
  <div style="padding: 20px">
    <el-tabs v-model="activeTab">
      <el-tab-pane label="Models" name="models">
        <ModelPanel />
      </el-tab-pane>
      <el-tab-pane label="Agents" name="agents">
        <AgentPanel :config="config" />
      </el-tab-pane>
      <el-tab-pane label="Channels" name="channels">
        <ChannelPanel />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import client from '@/api/client'
import ModelPanel from '@/components/config/ModelPanel.vue'
import AgentPanel from '@/components/config/AgentPanel.vue'
import ChannelPanel from '@/components/config/ChannelPanel.vue'
import { useChannelStore } from '@/stores/channels'

const activeTab = ref('models')
const config = ref<any>({})
const channelStore = useChannelStore()

onMounted(async () => {
  try {
    const { data } = await client.get('/config')
    config.value = data
  } catch {}
  channelStore.fetchStatus()
})
</script>
