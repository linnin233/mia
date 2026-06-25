<template>
  <div>
    <h3>Agent Model Assignments</h3>
    <div v-if="loading" style="color: #909399; padding: 20px">Loading...</div>
    <div v-else>
      <el-table :data="agents" stripe>
        <el-table-column prop="key" label="Agent" width="130" />
        <el-table-column label="Primary Model" width="200">
          <template #default="{ row }">
            <el-select
              v-if="row.hasModel"
              :model-value="row.model"
              size="small"
              style="width: 180px"
              @change="(val: string) => handleSave(row.key, { model: val })"
            >
              <el-option v-for="m in textModels" :key="m" :label="m" :value="m" />
            </el-select>
            <span v-else style="color: #909399">{{ row.model || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Fallback" width="200">
          <template #default="{ row }">
            <el-select
              v-if="row.hasFallback"
              :model-value="row.fallback"
              size="small"
              style="width: 180px"
              clearable
              @change="(val: string) => handleSave(row.key, { fallback: val || '' })"
            >
              <el-option v-for="m in textModels" :key="m" :label="m" :value="m" />
            </el-select>
            <span v-else style="color: #909399">-</span>
          </template>
        </el-table-column>
        <el-table-column label="Features" min-width="200">
          <template #default="{ row }">
            <template v-if="row.key === 'receiver'">
              <el-switch
                :model-value="cfg.receiver?.vision_enabled"
                size="small"
                active-text="Vision"
                style="margin-right: 8px"
                @change="(val: boolean) => handleSave('receiver', { vision_enabled: val })"
              />
              <el-switch
                :model-value="cfg.receiver?.audio_enabled"
                size="small"
                active-text="Audio"
                @change="(val: boolean) => handleSave('receiver', { audio_enabled: val })"
              />
            </template>
            <template v-else-if="row.key === 'sender'">
              <el-switch
                :model-value="cfg.sender?.tts_enabled"
                size="small"
                active-text="TTS"
                @change="(val: boolean) => handleSave('sender', { tts_enabled: val })"
              />
            </template>
            <span v-else style="color: #909399; font-size: 12px">-</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getModels, updateAgentConfig } from '@/api/config'
import client from '@/api/client'

const loading = ref(true)
const cfg = ref<any>({})
const textModels = ref<string[]>([])
const saving = ref<Record<string, boolean>>({})

const agents = [
  { key: 'scheduler', label: 'Scheduler', hasModel: true, hasFallback: true },
  { key: 'task', label: 'TaskAgent', hasModel: true, hasFallback: true },
  { key: 'memory', label: 'MemoryAgent', hasModel: true, hasFallback: true },
  { key: 'receiver', label: 'Receiver', hasModel: false, hasFallback: false },
  { key: 'sender', label: 'Sender', hasModel: false, hasFallback: false },
]

onMounted(async () => {
  try {
    const [configRes, modelsRes] = await Promise.all([
      client.get('/config'),
      getModels(),
    ])
    cfg.value = configRes.data
    // 只取已启用的文字模型作为可选项
    textModels.value = modelsRes.models
      .filter((m: any) => m.enabled && m.capabilities.includes('text_chat'))
      .map((m: any) => m.id)
  } catch {}
  loading.value = false
})

async function handleSave(agentKey: string, config: Record<string, any>) {
  saving.value[agentKey] = true
  try {
    await updateAgentConfig(agentKey, config)
    // 更新本地显示
    if (config.model && cfg.value[agentKey]) cfg.value[agentKey].model = config.model
    if (config.fallback !== undefined && cfg.value[agentKey]) cfg.value[agentKey].fallback = config.fallback
    if (config.vision_enabled !== undefined && cfg.value.receiver) cfg.value.receiver.vision_enabled = config.vision_enabled
    if (config.audio_enabled !== undefined && cfg.value.receiver) cfg.value.receiver.audio_enabled = config.audio_enabled
    if (config.tts_enabled !== undefined && cfg.value.sender) cfg.value.sender.tts_enabled = config.tts_enabled
  } catch {}
  saving.value[agentKey] = false
}
</script>
