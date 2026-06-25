<template>
  <div>
    <h3>Channel Configuration</h3>
    <div v-if="loading" style="color: #909399; padding: 20px">Loading...</div>
    <div v-else style="display: flex; flex-direction: column; gap: 16px; max-width: 600px">
      <el-card v-for="ch in channelList" :key="ch.key">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span style="font-weight: 500">{{ ch.label }}</span>
            <el-switch
              :model-value="ch.enabled"
              :loading="ch.toggling"
              @change="(val: boolean) => handleToggle(ch.key, val)"
            />
          </div>
        </template>
        <div style="display: flex; flex-direction: column; gap: 10px; font-size: 13px">
          <!-- Status tags -->
          <div>
            <el-tag :type="ch.hasToken ? 'success' : 'warning'" size="small">
              {{ ch.hasToken ? 'Token configured' : 'No token' }}
            </el-tag>
            <el-tag :type="ch.enabled ? 'success' : 'info'" size="small" style="margin-left: 6px">
              {{ ch.enabled ? 'Enabled' : 'Disabled' }}
            </el-tag>
          </div>

          <!-- Token editing -->
          <div v-if="ch.editing">
            <el-input
              v-model="ch.editToken"
              type="password"
              show-password
              placeholder="Paste Bot Token here"
              size="small"
              style="margin-bottom: 6px"
            />
            <div style="display: flex; gap: 6px">
              <el-button size="small" type="primary" :loading="ch.saving" @click="handleSaveToken(ch.key)">
                Save
              </el-button>
              <el-button size="small" @click="ch.editing = false; ch.editToken = ''">Cancel</el-button>
            </div>
          </div>
          <div v-else>
            <div v-if="ch.hasToken && ch.detail">
              <span style="color: #606266">Token: </span>
              <code style="background: #f5f5f5; padding: 1px 6px; border-radius: 3px">{{ ch.detail.token_masked || '-' }}</code>
            </div>
            <div v-if="ch.detail?.file_size">
              <span style="color: #909399; font-size: 12px">
                {{ (ch.detail.file_size / 1024).toFixed(1) }} KB
                | {{ ch.detail.file_mtime || '-' }}
              </span>
            </div>
            <div style="margin-top: 4px">
              <el-button size="small" @click="ch.editing = true">
                {{ ch.hasToken ? 'Update Token' : 'Set Token' }}
              </el-button>
            </div>
          </div>

          <!-- File path -->
          <div style="font-size: 11px; color: #c0c4cc; word-break: break-all">
            {{ ch.detail?.token_file || ('~/.mia/' + ch.key + '_bot_token') }}
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useChannelStore } from '@/stores/channels'
import { getInterfaceDetail, updateInterfaceToken } from '@/api/channels'
import { ElMessage } from 'element-plus'

const channelStore = useChannelStore()
const loading = ref(true)
const toggling = ref<Record<string, boolean>>({})
const details = ref<Record<string, any>>({})

// Editable channel state
const editState = reactive<Record<string, { editing: boolean; editToken: string; saving: boolean }>>({
  wechat: { editing: false, editToken: '', saving: false },
  telegram: { editing: false, editToken: '', saving: false },
})

onMounted(async () => {
  await channelStore.fetchStatus()
  for (const name of ['wechat', 'telegram']) {
    try {
      details.value[name] = await getInterfaceDetail(name)
    } catch {
      details.value[name] = null
    }
  }
  loading.value = false
})

const channelList = computed(() => [
  {
    key: 'wechat',
    label: 'WeChat (iLink Bot)',
    enabled: channelStore.channels.wechat?.enabled ?? false,
    hasToken: channelStore.channels.wechat?.has_token ?? false,
    toggling: toggling.value['wechat'] ?? false,
    detail: details.value['wechat'],
    editing: editState.wechat.editing,
    editToken: editState.wechat.editToken,
    saving: editState.wechat.saving,
  },
  {
    key: 'telegram',
    label: 'Telegram (Bot API)',
    enabled: channelStore.channels.telegram?.enabled ?? false,
    hasToken: channelStore.channels.telegram?.has_token ?? false,
    toggling: toggling.value['telegram'] ?? false,
    detail: details.value['telegram'],
    editing: editState.telegram.editing,
    editToken: editState.telegram.editToken,
    saving: editState.telegram.saving,
  },
])

async function handleToggle(name: string, enabled: boolean) {
  toggling.value[name] = true
  try {
    await channelStore.toggle(name, enabled)
    details.value[name] = await getInterfaceDetail(name)
  } catch {
  } finally {
    toggling.value[name] = false
  }
}

async function handleSaveToken(name: string) {
  const st = editState[name]
  if (!st.editToken.trim()) return
  st.saving = true
  try {
    await updateInterfaceToken(name, st.editToken.trim())
    details.value[name] = await getInterfaceDetail(name)
    st.editing = false
    st.editToken = ''
    ElMessage.success(`${name} token updated`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || 'Failed to save token')
  } finally {
    st.saving = false
  }
}
</script>
