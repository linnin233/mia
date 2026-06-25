<template>
  <div style="display: flex; flex-direction: column; height: 100%">
    <MessageBox :messages="chatStore.messages" :loading="chatStore.loading" />
    <ChatInput :loading="chatStore.loading" @send="chatStore.send" />
  </div>
</template>

<script setup lang="ts">
import { useChatStore } from '@/stores/chat'
import MessageBox from '@/components/chat/MessageBox.vue'
import ChatInput from '@/components/chat/ChatInput.vue'

const chatStore = useChatStore()
</script>
