<template>
  <div style="padding: 20px">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px">
      <h2 style="margin: 0">Sessions</h2>
      <el-button type="primary" @click="showCreate = true">New Session</el-button>
    </div>

    <el-table :data="sessionStore.sessions" v-loading="sessionStore.loading" stripe>
      <el-table-column prop="name" label="Name" min-width="150" />
      <el-table-column label="Source" width="100">
        <template #default="{ row }">
          <el-tag size="small">{{ sourceLabel(row.source) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="turn_count" label="Turns" width="80" />
      <el-table-column prop="created_at" label="Created" width="170">
        <template #default="{ row }">{{ row.created_at?.slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="Actions" width="260">
        <template #default="{ row }">
          <el-button
            size="small"
            :type="row.session_id === sessionStore.currentId ? 'success' : 'default'"
            @click="handleActivate(row.session_id)"
          >
            {{ row.session_id === sessionStore.currentId ? 'Active' : 'Switch' }}
          </el-button>
          <el-button size="small" @click="handleRename(row)">Rename</el-button>
          <el-button size="small" type="danger" @click="handleDelete(row.session_id)" :disabled="sessionStore.sessions.length <= 1">
            Delete
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" title="New Session" width="400px">
      <el-input v-model="newName" placeholder="Session name" @keyup.enter="handleCreate" />
      <template #footer>
        <el-button @click="showCreate = false">Cancel</el-button>
        <el-button type="primary" @click="handleCreate" :disabled="!newName.trim()">Create</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRename" title="Rename Session" width="400px">
      <el-input v-model="renameValue" placeholder="New name" @keyup.enter="handleRenameConfirm" />
      <template #footer>
        <el-button @click="showRename = false">Cancel</el-button>
        <el-button type="primary" @click="handleRenameConfirm">Confirm</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useSessionStore } from '@/stores/sessions'
import type { SessionInfo } from '@/types'

const sessionStore = useSessionStore()

const showCreate = ref(false)
const showRename = ref(false)
const newName = ref('')
const renameTarget = ref<SessionInfo | null>(null)
const renameValue = ref('')

onMounted(() => sessionStore.fetchSessions())

function sourceLabel(source: string) {
  return { cli: 'CLI', wechat: 'WeChat', telegram: 'TG', api: 'API' }[source] || source
}

async function handleCreate() {
  if (!newName.value.trim()) return
  await sessionStore.create(newName.value.trim())
  newName.value = ''
  showCreate.value = false
}

function handleRename(row: SessionInfo) {
  renameTarget.value = row
  renameValue.value = row.name
  showRename.value = true
}

async function handleRenameConfirm() {
  if (!renameTarget.value || !renameValue.value.trim()) return
  await sessionStore.rename(renameTarget.value.session_id, renameValue.value.trim())
  showRename.value = false
}

async function handleActivate(id: string) {
  await sessionStore.activate(id)
}

async function handleDelete(id: string) {
  try {
    await sessionStore.remove(id)
  } catch {}
}
</script>
