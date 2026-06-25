<template>
  <h3>Channel Configuration</h3>
  <div style="display: flex; flex-direction: column; gap: 16px; max-width: 400px">
    <el-card v-for="ch in channelList" :key="ch.key">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ ch.label }}</span>
          <el-switch
            :model-value="ch.enabled"
            @change="(val: boolean) => channelStore.toggle(ch.key, val)"
          />
        </div>
      </template>
      <div>
        <el-tag :type="ch.hasToken ? 'success' : 'danger'" size="small">
          {{ ch.hasToken ? 'Token configured' : 'No token' }}
        </el-tag>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useChannelStore } from '@/stores/channels'

const channelStore = useChannelStore()

const channelList = computed(() => [
  { key: 'wechat', label: 'WeChat (iLink Bot)', enabled: channelStore.channels.wechat?.enabled, hasToken: channelStore.channels.wechat?.has_token },
  { key: 'telegram', label: 'Telegram (Bot API)', enabled: channelStore.channels.telegram?.enabled, hasToken: channelStore.channels.telegram?.has_token },
])
</script>
