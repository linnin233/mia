<template>
  <h3>Model Registry</h3>
  <el-table :data="models" stripe>
    <el-table-column prop="id" label="Model ID" width="180" />
    <el-table-column prop="provider" label="Provider" width="100" />
    <el-table-column prop="desc" label="Description" min-width="300" />
    <el-table-column label="Capabilities" min-width="250">
      <template #default="{ row }">
        <el-tag v-for="cap in row.capabilities" :key="cap" size="small" style="margin-right: 4px">
          {{ cap }}
        </el-tag>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
const models = [
  { id: 'mimo-v2.5-pro', provider: 'MiMo', desc: 'MiMo V2.5 Pro flagship text model (1M context)', capabilities: ['TEXT_CHAT', 'STREAMING'] },
  { id: 'mimo-v2.5', provider: 'MiMo', desc: 'MiMo V2.5 multimodal (text+vision+audio)', capabilities: ['TEXT_CHAT', 'VISION', 'AUDIO_UNDERSTANDING', 'STREAMING'] },
  { id: 'mimo-v2.5-asr', provider: 'MiMo', desc: 'MiMo V2.5 ASR speech recognition', capabilities: ['ASR'] },
  { id: 'mimo-v2.5-tts', provider: 'MiMo', desc: 'MiMo V2.5 TTS speech synthesis', capabilities: ['TTS', 'STREAMING'] },
  { id: 'deepseek-v4-pro', provider: 'DeepSeek', desc: 'DeepSeek V4 Pro flagship (1.6T/49B MoE, 1M ctx)', capabilities: ['TEXT_CHAT', 'STREAMING'] },
  { id: 'deepseek-v4-flash', provider: 'DeepSeek', desc: 'DeepSeek V4 Flash high-throughput (284B/13B)', capabilities: ['TEXT_CHAT', 'STREAMING'] },
]
</script>
