<template>
  <div>
    <h3>Channel Configuration</h3>
    <div v-if="loading" style="color: #909399; padding: 20px">Loading...</div>
    <div v-else style="display: flex; flex-direction: column; gap: 16px; max-width: 660px">
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
        <div style="display: flex; flex-direction: column; gap: 8px; font-size: 13px; line-height: 1.8">
          <!-- Status row -->
          <div>
            <el-tag :type="ch.hasToken ? 'success' : 'warning'" size="small">{{ ch.hasToken ? 'Bound' : 'Not bound' }}</el-tag>
            <el-tag :type="ch.enabled ? 'success' : 'info'" size="small" style="margin-left: 6px">{{ ch.enabled ? 'Enabled' : 'Disabled' }}</el-tag>
            <span style="margin-left: 8px; color: #909399; font-size: 12px">{{ ch.detail?.login_method || '-' }}</span>
          </div>

          <!-- Token info -->
          <div v-if="ch.hasToken && ch.detail?.token_masked" style="background: #fafafa; padding: 8px 12px; border-radius: 4px">
            <div><span style="color: #606266">Token: </span><code>{{ ch.detail.token_masked }}</code></div>
            <div style="color: #909399; font-size: 12px">File: {{ ch.detail.token_file }}</div>
            <div style="color: #909399; font-size: 12px" v-if="ch.detail.file_size">
              Size: {{ (ch.detail.file_size / 1024).toFixed(1) }} KB
              | Updated: {{ ch.detail.file_mtime || '-' }}
            </div>
            <div style="color: #909399; font-size: 12px">API: {{ ch.detail.base_url }}</div>
          </div>

          <!-- WeChat context tokens (unique to WeChat) -->
          <div v-if="ch.key === 'wechat' && ch.detail?.ctx_file" style="background: #f0f9eb; padding: 8px 12px; border-radius: 4px">
            <div style="color: #67c23a; font-weight: 500; font-size: 12px">Context Tokens (User Routing Cache)</div>
            <div style="color: #909399; font-size: 12px">
              {{ ch.detail.ctx_user_count ?? 0 }} active users
              | {{ ((ch.detail.ctx_file_size ?? 0) / 1024).toFixed(1) }} KB
              | {{ ch.detail.ctx_file_mtime || '-' }}
            </div>
            <div style="color: #c0c4cc; font-size: 11px; word-break: break-all">{{ ch.detail.ctx_file }}</div>
          </div>

          <!-- No token state -->
          <div v-if="!ch.hasToken" style="background: #fef0f0; padding: 8px 12px; border-radius: 4px; color: #f56c6c; font-size: 12px">
            <div v-if="ch.key === 'wechat'">Not logged in. Use CLI <code>/interface</code> for QR scan, or paste token below.</div>
            <div v-else>No token configured. Get one from @BotFather on Telegram.</div>
          </div>

          <!-- Token editing -->
          <div v-if="editState[ch.key].editing" style="margin-top: 4px">
            <el-input
              v-model="editState[ch.key].editToken"
              type="password"
              show-password
              placeholder="Paste token here"
              size="small"
              style="margin-bottom: 6px"
            />
            <div style="display: flex; gap: 6px">
              <el-button size="small" type="primary" :loading="editState[ch.key].saving" @click="handleSaveToken(ch.key)">Save</el-button>
              <el-button size="small" @click="cancelEdit(ch.key)">Cancel</el-button>
            </div>
          </div>
          <div v-else>
            <el-button size="small" @click="startEdit(ch.key)">
              {{ ch.hasToken ? 'Update Token' : 'Set Token' }}
            </el-button>
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

const editState = reactive<Record<string, { editing: boolean; editToken: string; saving: boolean }>>({
  wechat: { editing: false, editToken: '', saving: false },
  telegram: { editing: false, editToken: '', saving: false },
})

onMounted(async () => {
  await channelStore.fetchStatus()
  for (const name of ['wechat', 'telegram']) {
    try { details.value[name] = await getInterfaceDetail(name) } catch { details.value[name] = null }
  }
  loading.value = false
})

const channelList = computed(() => [
  { key: 'wechat' as const, label: 'WeChat (iLink Bot)', enabled: channelStore.channels.wechat?.enabled ?? false, hasToken: channelStore.channels.wechat?.has_token ?? false, toggling: toggling.value['wechat'] ?? false, detail: details.value['wechat'] },
  { key: 'telegram' as const, label: 'Telegram (Bot API)', enabled: channelStore.channels.telegram?.enabled ?? false, hasToken: channelStore.channels.telegram?.has_token ?? false, toggling: toggling.value['telegram'] ?? false, detail: details.value['telegram'] },
])

function startEdit(name: string) { editState[name].editing = true; editState[name].editToken = '' }
function cancelEdit(name: string) { editState[name].editing = false; editState[name].editToken = '' }

async function handleToggle(name: string, enabled: boolean) {
  toggling.value[name] = true
  try { await channelStore.toggle(name, enabled); details.value[name] = await getInterfaceDetail(name) } catch {} finally { toggling.value[name] = false }
}

async function handleSaveToken(name: string) {
  const st = editState[name]
  if (!st.editToken.trim()) return
  st.saving = true
  try {
    await updateInterfaceToken(name, st.editToken.trim())
    details.value[name] = await getInterfaceDetail(name)
    st.editing = false; st.editToken = ''
    ElMessage.success(`${name} token updated`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || 'Failed')
  } finally { st.saving = false }
}
</script>
