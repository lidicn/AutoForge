<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { NAlert, NButton, NCard, NSpace, NSpin, NTag } from 'naive-ui'
import { facade } from '@/api'
import type { GraphResponse } from '@/types/api'
import DiagnosticPanel from '@/components/DiagnosticPanel.vue'
import SafetyAlert from '@/components/SafetyAlert.vue'

const route = useRoute()
const name = route.params.name as string
const irData = ref<GraphResponse | null>(null)
const loading = ref(true)
const error = ref('')
const deploying = ref(false)
const deployMsg = ref('')

onMounted(async () => {
  try {
    const res = await facade.graph(name)
    irData.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})

async function deployWatch() {
  if (!irData.value?.ir) return
  deploying.value = true
  deployMsg.value = ''
  try {
    const base = import.meta.env.VITE_API_BASE ?? 'http://localhost:8787/api'
    const res = await fetch(`${base}/watch/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ir: irData.value.ir, dry_live: true }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.error || data.detail?.[0]?.msg || '部署失败')
    deployMsg.value = `已部署（dry-live）`
  } catch (e: any) {
    deployMsg.value = `部署失败：${e.message || String(e)}`
  } finally {
    deploying.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <RouterLink to="/automations" class="back">← 返回列表</RouterLink>
      <h1>{{ name }}</h1>
      <NSpace style="margin-top: 8px">
        <NButton type="primary" :loading="deploying" @click="deployWatch">
          部署为常驻 watch（dry-live）
        </NButton>
        <NTag v-if="deployMsg" :type="deployMsg.includes('失败') ? 'error' : 'success'">
          {{ deployMsg }}
        </NTag>
      </NSpace>
    </div>

    <n-alert v-if="error" type="error" title="加载失败">{{ error }}</n-alert>

    <n-spin v-else :show="loading">
      <template v-if="irData">
        <SafetyAlert :diagnostics="irData.diagnostics" />

        <n-card title="自然语言描述" :bordered="false" class="block">
          <p class="nl">{{ irData.nl }}</p>
        </n-card>

        <DiagnosticPanel :diagnostics="irData.diagnostics" />

        <n-card title="IR 原文（只读）" :bordered="false" class="block">
          <pre class="code">{{ JSON.stringify(irData.ir, null, 2) }}</pre>
        </n-card>
      </template>
    </n-spin>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 20px;
}
.page-head h1 {
  margin: 6px 0 0;
  font-size: 24px;
  font-family: monospace;
}
.back {
  text-decoration: none;
  color: #6b7280;
  font-size: 13px;
}
.back:hover {
  color: #4f46e5;
}
.block {
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.nl {
  margin: 0;
  line-height: 1.8;
  font-size: 15px;
}
.code {
  margin: 0;
  background: #1e1e2e;
  color: #d4d4d4;
  padding: 16px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
}
</style>
