<template>
  <div :style="{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: '12px' }">
    <div :style="{
      maxWidth: '70%',
      padding: '10px 14px',
      borderRadius: '12px',
      background: msg.role === 'user' ? '#409EFF' : '#f0f0f0',
      color: msg.role === 'user' ? '#fff' : '#303133',
      fontSize: '14px',
      lineHeight: '1.5',
      wordBreak: 'break-word',
    }">
      <div style="font-size: 11px; margin-bottom: 4px; opacity: 0.7">
        {{ msg.role === 'user' ? 'You' : 'MIA' }}
      </div>
      <div style="white-space: pre-wrap">{{ msg.content }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{ msg: ChatMessage }>()
</script>
