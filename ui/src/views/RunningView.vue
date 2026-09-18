<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'
import type { WatchInstance } from '@/types/api'

const loading = ref(false)
const stopping = ref('')
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

async function stop(owner: string) {
  stopping.value = owner
  try {
    await fetch(`${import.meta.env.VITE_API_BASE ?? 'http://localhost:8787/api'}/watch/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ owner }),
    })
    await load()
  } catch (e: any) {
    error.value = e.message || String(e)
  } finally {
    stopping.value = ''
  }
}

onMounted(load)
</script>

<template>
  <n-space vertical size="large" style="max-width: 960px">
    <n-h1>运行中</n-h1>
    <n-text depth="2">正在跑的 watch 进程（由 <code>forge watch</code> 持有）。数据来自 persist 目录的 sidecar 文件。</n-text>

    <n-space>
      <n-button @click="load" :loading="loading" type="primary">刷新</n-button>
    </n-space>

    <n-alert v-if="error" type="error" :title="error" />
    <n-empty v-if="!loading && watches.length === 0" description="当前没有运行中的 watch 实例" />

    <n-space v-for="w in watches" :key="w.owner + w.acquired_at" vertical size="small">
      <n-card>
        <n-space vertical size="small">
          <n-text strong style="font-size: 16px">{{ w.name || w.graph }}</n-text>
          <n-text depth="3" size="small">{{ w.automation_id }} · {{ w.node_count }} 节点</n-text>
          <n-divider style="margin: 6px 0" />
          <n-text depth="2" size="small">触发：</n-text>
          <n-text size="small" v-for="t in w.triggers" :key="t">{{ t }}</n-text>
          <n-text depth="2" size="small">动作：</n-text>
          <n-text size="small" v-for="a in w.actions" :key="a">{{ a }}</n-text>
          <n-divider style="margin: 6px 0" />
          <n-text depth="3" size="small">持有者：{{ w.owner }}</n-text>
          <n-text depth="3" size="small">启动：{{ w.acquired_at }}</n-text>
          <n-text depth="3" size="small">HA：{{ w.ha_url }}</n-text>
          <n-button
            size="small"
            type="error"
            :loading="stopping === w.owner"
            @click="stop(w.owner)"
            style="margin-top: 8px"
          >停止</n-button>
        </n-space>
      </n-card>
    </n-space>
  </n-space>
</template>
