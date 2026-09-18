<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NButton, NCard, NEmpty, NSpace, NText, NDivider, NAlert } from 'naive-ui'
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
    const base = import.meta.env.VITE_API_BASE ?? 'http://localhost:8787/api'
    await fetch(`${base}/watch/stop?owner=${encodeURIComponent(owner)}`, { method: 'POST' })
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
  <NSpace vertical size="large" style="max-width: 960px">
    <h2>运行中</h2>
    <NText depth="2">正在跑的 watch 进程（由 <code>forge watch</code> 持有）。数据来自 persist 目录的 sidecar 文件。</NText>

    <NSpace>
      <NButton @click="load" :loading="loading" type="primary">刷新</NButton>
    </NSpace>

    <NAlert v-if="error" type="error" :title="error" />
    <NEmpty v-if="!loading && watches.length === 0" description="当前没有运行中的 watch 实例" />

    <NSpace v-for="w in watches" :key="w.owner + w.acquired_at" vertical size="small">
      <NCard>
        <NSpace vertical size="small">
          <NText strong style="font-size: 16px">{{ w.name || w.graph }}</NText>
          <NText depth="3" size="small">{{ w.automation_id }} · {{ w.node_count }} 节点</NText>
          <NDivider style="margin: 6px 0" />
          <NText depth="2" size="small">触发：</NText>
          <NText size="small" v-for="t in w.triggers" :key="t">{{ t }}</NText>
          <NText depth="2" size="small">动作：</NText>
          <NText size="small" v-for="a in w.actions" :key="a">{{ a }}</NText>
          <NDivider style="margin: 6px 0" />
          <NText depth="3" size="small">持有者：{{ w.owner }}</NText>
          <NText depth="3" size="small">启动：{{ w.acquired_at }}</NText>
          <NText depth="3" size="small">HA：{{ w.ha_url }}</NText>
          <NButton
            size="small"
            type="error"
            :loading="stopping === w.owner"
            @click="stop(w.owner)"
            style="margin-top: 8px"
          >停止</NButton>
        </NSpace>
      </NCard>
    </NSpace>
  </NSpace>
</template>
