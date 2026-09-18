<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'
import type { WatchInstance } from '@/types/api'

const loading = ref(false)
const watches = ref<WatchInstance[]>([])
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.watchList()
    watches.value = data.watches ?? []
  } catch (e: any) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <n-space vertical size="large" style="max-width: 960px">
    <n-h1>运行中</n-h1>
    <n-text depth="2">
      列出当前在跑的 watch 实例（由 <code>forge watch</code> 进程持有）。
      数据来自 persist 目录下的 <code>watch.lock.info</code>，只读视图。
    </n-text>

    <n-button @click="load" :loading="loading" type="primary">刷新</n-button>

    <n-alert v-if="error" type="error" :title="error" />

    <n-empty v-if="!loading && watches.length === 0" description="当前没有运行中的 watch 实例" />

    <n-table
      v-if="watches.length"
      :columns="[
        { title: '自动化 (IR)', key: 'graph' },
        { title: '持有者', key: 'owner' },
        { title: '启动时间', key: 'acquired_at' },
        { title: 'HA 地址', key: 'ha_url' },
      ]"
      :data="watches"
      bordered
    />
  </n-space>
</template>
