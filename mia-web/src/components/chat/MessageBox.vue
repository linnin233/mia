<template>
  <div ref="container" style="flex: 1; overflow-y: auto; padding: 16px 20px">
    <MessageBubble v-for="msg in messages" :key="msg.id" :msg="msg" />
    <div v-if="loading" style="color: #909399; text-align: center; padding: 12px">
      <el-icon class="is-loading"><Loading /></el-icon>
      Thinking...
    </div>
    <div ref="bottom" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import MessageBubble from './MessageBubble.vue'
import type { ChatMessage } from '@/types'

const props = defineProps<{
  messages: ChatMessage[]
  loading: boolean
}>()

const container = ref<HTMLElement>()
const bottom = ref<HTMLElement>()

function scrollToBottom() {
  nextTick(() => {
    bottom.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

watch(() => props.messages.length, scrollToBottom)
watch(() => props.loading, scrollToBottom)
onMounted(scrollToBottom)
</script>
