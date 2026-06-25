<template>
  <h3>Agent Model Assignments</h3>
  <el-table :data="agents" stripe>
    <el-table-column prop="agent" label="Agent" width="150" />
    <el-table-column prop="model" label="Primary Model" width="180" />
    <el-table-column prop="fallback" label="Fallback Model" width="180" />
    <el-table-column prop="features" label="Features" min-width="200">
      <template #default="{ row }">
        <el-tag v-for="f in row.features" :key="f" size="small" :type="f.startsWith('OFF') ? 'info' : 'success'" style="margin-right: 4px">
          {{ f }}
        </el-tag>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ config: any }>()

const agents = computed(() => {
  const c = props.config || {}
  return [
    { agent: 'Scheduler', model: c.scheduler?.model || '-', fallback: c.scheduler?.fallback || '-', features: [] },
    { agent: 'TaskAgent', model: c.task?.model || '-', fallback: c.task?.fallback || '-', features: [] },
    { agent: 'MemoryAgent', model: c.memory?.model || '-', fallback: c.memory?.fallback || '-', features: [] },
    {
      agent: 'Receiver',
      model: c.receiver?.text_model || '-',
      fallback: '-',
      features: [
        c.receiver?.vision_enabled ? 'Vision ON' : 'Vision OFF',
        c.receiver?.audio_enabled ? 'Audio ON' : 'Audio OFF',
      ],
    },
    {
      agent: 'Sender',
      model: c.sender?.tts_model || '-',
      fallback: '-',
      features: [c.sender?.tts_enabled ? 'TTS ON' : 'TTS OFF'],
    },
  ]
})
</script>
