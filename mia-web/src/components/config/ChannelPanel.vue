<template>
  <div>
    <h3>渠道配置</h3>
    <div v-if="loading" style="color: #909399; padding: 20px">加载中...</div>
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
            <el-tag :type="ch.hasToken ? 'success' : 'warning'" size="small">{{ ch.hasToken ? '已绑定' : '未绑定' }}</el-tag>
            <el-tag :type="ch.enabled ? 'success' : 'info'" size="small" style="margin-left: 6px">{{ ch.enabled ? '启用' : '已关闭' }}</el-tag>
            <span style="margin-left: 8px; color: #909399; font-size: 12px">{{ ch.detail?.login_method || '-' }}</span>
          </div>

          <!-- Token info -->
          <div v-if="ch.hasToken && ch.detail?.token_masked" style="background: #fafafa; padding: 8px 12px; border-radius: 4px">
            <div><span style="color: #606266">Token: </span><code>{{ ch.detail.token_masked }}</code></div>
            <div style="color: #909399; font-size: 12px">文件: {{ ch.detail.token_file }}</div>
            <div style="color: #909399; font-size: 12px" v-if="ch.detail.file_size">
              大小: {{ (ch.detail.file_size / 1024).toFixed(1) }} KB
              | 更新: {{ ch.detail.file_mtime || '-' }}
            </div>
            <div style="color: #909399; font-size: 12px">API: {{ ch.detail.base_url }}</div>
          </div>

          <!-- We聊天 context tokens (unique to We聊天) -->
          <div v-if="ch.key === 'wechat' && ch.detail?.ctx_file" style="background: #f0f9eb; padding: 8px 12px; border-radius: 4px">
            <div style="color: #67c23a; font-weight: 500; font-size: 12px">Context Tokens (用户路由缓存)</div>
            <div style="color: #909399; font-size: 12px">
              {{ ch.detail.ctx_user_count ?? 0 }} 活跃用户
              | {{ ((ch.detail.ctx_file_size ?? 0) / 1024).toFixed(1) }} KB
              | {{ ch.detail.ctx_file_mtime || '-' }}
            </div>
            <div style="color: #c0c4cc; font-size: 11px; word-break: break-all">{{ ch.detail.ctx_file }}</div>
          </div>

          <!-- 无 Token state -->
          <div v-if="!ch.hasToken" style="background: #fef0f0; padding: 8px 12px; border-radius: 4px; color: #f56c6c; font-size: 12px">
            <div v-if="ch.key === 'wechat'">未登录。请用 CLI <code>/interface</code> 扫码登录，或在下方粘贴 Token。</div>
            <div v-else>未配置 Token。请在 Telegram 找 @BotFather 获取。</div>
          </div>

          <!-- QR code login (WeChat only) -->
          <div v-if="ch.key === 'wechat' && !ch.hasToken" style="margin-top: 4px">
            <el-button type="success" size="small" :loading="qrLoading" @click="startQrLogin">
              微信扫码登录
            </el-button>
          </div>

          <!-- Token editing -->
          <div v-if="editState[ch.key].editing" style="margin-top: 4px">
            <el-input
              v-model="editState[ch.key].editToken"
              type="password"
              show-password
              placeholder="在此粘贴 Token"
              size="small"
              style="margin-bottom: 6px"
            />
            <div style="display: flex; gap: 6px">
              <el-button size="small" type="primary" :loading="editState[ch.key].saving" @click="handleSaveToken(ch.key)">保存</el-button>
              <el-button size="small" @click="cancelEdit(ch.key)">取消</el-button>
            </div>
          </div>
          <div v-else>
            <el-button size="small" @click="startEdit(ch.key)">
              {{ ch.hasToken ? '更新 Token' : '设置 Token' }}
            </el-button>
          </div>
        </div>
      </el-card>
    </div>

    <!-- QR Code Login Dialog -->
    <el-dialog v-model="qrDialogVisible" title="微信扫码登录" width="380px" @close="stopQrPolling">
      <div style="text-align: center">
        <div v-if="qrStatus === 'waiting'" style="color: #909399; margin-bottom: 12px">
          请使用手机微信扫描下方二维码
        </div>
        <div v-else-if="qrStatus === 'scanned'" style="color: #409EFF; margin-bottom: 12px">
          已扫描，请在手机上确认登录
        </div>
        <div v-else-if="qrStatus === 'confirmed'" style="color: #67c23a; margin-bottom: 12px">
          登录成功！
        </div>
        <div v-else-if="qrStatus === 'expired'" style="color: #f56c6c; margin-bottom: 12px">
          二维码已过期，请重新获取
        </div>
        <img v-if="qrImage" :src="qrImage" style="max-width: 100%; border: 1px solid #eee; border-radius: 8px" />
        <div v-if="!qrImage && qrLoading" style="padding: 40px; color: #909399">
          正在获取二维码...
        </div>
        <div v-if="qrStatus === 'expired'" style="margin-top: 12px">
          <el-button size="small" type="primary" @click="startQrLogin">重新获取</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useChannelStore } from '@/stores/channels'
import { getInterfaceDetail, updateInterfaceToken } from '@/api/channels'
import client from '@/api/client'
import { ElMessage } from 'element-plus'

const channelStore = useChannelStore()
const loading = ref(true)
const toggling = ref<Record<string, boolean>>({})
const details = ref<Record<string, any>>({})

const editState = reactive<Record<string, { editing: boolean; editToken: string; saving: boolean }>>({
  wechat: { editing: false, editToken: '', saving: false },
  telegram: { editing: false, editToken: '', saving: false },
})

// QR code login state
const qrDialogVisible = ref(false)
const qrLoading = ref(false)
const qrImage = ref('')
const qrCode = ref('')
const qrStatus = ref('')
let qrTimer: any = null

onMounted(async () => {
  await channelStore.fetchStatus()
  for (const name of ['wechat', 'telegram']) {
    try { details.value[name] = await getInterfaceDetail(name) } catch { details.value[name] = null }
  }
  loading.value = false
})

const channelList = computed(() => [
  { key: 'wechat' as const, label: '微信 (iLink Bot)', enabled: channelStore.channels.wechat?.enabled ?? false, hasToken: channelStore.channels.wechat?.has_token ?? false, toggling: toggling.value['wechat'] ?? false, detail: details.value['wechat'] },
  { key: 'telegram' as const, label: '纸飞机 (Bot API)', enabled: channelStore.channels.telegram?.enabled ?? false, hasToken: channelStore.channels.telegram?.has_token ?? false, toggling: toggling.value['telegram'] ?? false, detail: details.value['telegram'] },
])

async function startQrLogin() {
  qrLoading.value = true
  qrDialogVisible.value = true
  qrImage.value = ''
  qrStatus.value = ''
  try {
    const { data } = await client.post('/interface/wechat/qrcode')
    qrCode.value = data.qrcode
    if (data.image) {
      qrImage.value = data.image.startsWith('data:') ? data.image : 'data:image/png;base64,' + data.image
    }
    qrStatus.value = 'waiting'
    startQrPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '获取二维码失败')
    qrDialogVisible.value = false
  } finally {
    qrLoading.value = false
  }
}

function startQrPolling() {
  stopQrPolling()
  qrTimer = setInterval(async () => {
    if (!qrCode.value) return
    try {
      const { data } = await client.get(`/interface/wechat/qrcode/${qrCode.value}`)
      qrStatus.value = data.status
      if (data.status === 'confirmed') {
        stopQrPolling()
        const name = 'wechat'
        details.value[name] = await getInterfaceDetail(name)
        await channelStore.fetchStatus()
        setTimeout(() => { qrDialogVisible.value = false }, 1500)
      } else if (data.status === 'expired' || data.status === 'timeout') {
        stopQrPolling()
      }
    } catch {}
  }, 2000)
}

function stopQrPolling() {
  if (qrTimer) { clearInterval(qrTimer); qrTimer = null }
}

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
    ElMessage.success(`${name} Token 已更新`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || '失败')
  } finally { st.saving = false }
}
</script>
