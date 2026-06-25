<template>
  <div style="padding: 20px">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px">
      <h2 style="margin: 0">Memory ({{ memStore.total }} entries)</h2>
      <el-button type="warning" @click="handleCompact" :loading="compacting">Compact</el-button>
    </div>

    <el-table :data="memStore.entries" v-loading="memStore.loading" stripe>
      <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: 12px">
            <p><strong>Content:</strong> {{ row.content }}</p>
            <p><strong>Confidence:</strong> {{ (row.confidence * 100).toFixed(0) }}%</p>
            <p><strong>Keywords:</strong> {{ row.keywords?.join(', ') }}</p>
            <p><strong>Source Sessions:</strong> {{ row.source_sessions?.join(', ') }}</p>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="Content" min-width="300" show-overflow-tooltip />
      <el-table-column label="Category" width="110">
        <template #default="{ row }">
          <el-tag size="small">{{ row.category }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Confidence" width="110">
        <template #default="{ row }">
          <el-progress :percentage="Math.round(row.confidence * 100)" :stroke-width="8" />
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="Date" width="180">
        <template #default="{ row }">{{ row.created_at?.slice(0, 16) }}</template>
      </el-table-column>
    </el-table>

    <div style="display: flex; justify-content: center; margin-top: 16px">
      <el-pagination
        v-model:current-page="memStore.page"
        :page-size="memStore.pageSize"
        :total="memStore.total"
        layout="prev, pager, next"
        @current-change="memStore.fetchEntries"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useMemoryStore } from '@/stores/memory'

const memStore = useMemoryStore()
const compacting = ref(false)

onMounted(() => memStore.fetchEntries(1))

function onExpand(row: any, rows: any[]) {}

async function handleCompact() {
  compacting.value = true
  try {
    await memStore.compact()
  } finally {
    compacting.value = false
  }
}
</script>
