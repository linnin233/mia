<template>
  <div style="border-top: 1px solid #e4e7ed; padding: 12px 20px; display: flex; gap: 8px; align-items: flex-end">
    <el-input
      v-model="text"
      type="textarea"
      :rows="2"
      placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
      resize="none"
      :disabled="loading"
      @keydown.enter.exact.prevent="handleSend"
    />
    <el-button type="primary" :disabled="!text.trim() || loading" @click="handleSend" :loading="loading">
      Send
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ loading: boolean }>()
const emit = defineEmits<{ send: [text: string] }>()

const text = ref('')

function handleSend() {
  const msg = text.value.trim()
  if (!msg) return
  emit('send', msg)
  text.value = ''
}
</script>
