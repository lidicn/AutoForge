<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { NAlert, NButton, NCard, NSpace, NSpin, NTag, NText } from 'naive-ui'
import { facade } from '@/api'
import type { GraphResponse } from '@/types/api'
import DiagnosticPanel from '@/components/DiagnosticPanel.vue'
import SafetyAlert from '@/components/SafetyAlert.vue'

const route = useRoute()
const name = route.params.name as string
const irData = ref<GraphResponse | null>(null)
// v1.27 D10：运行期事实（触发沿）——GET /api/automations/{name} 的卡片口径
const card = ref<Record<string, unknown> | null>(null)
const cardError = ref('')
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
  // 触发沿是**另一条数据源**（卡片口径），读失败不拖垮整页：⛔ 不把"读不到"显示成"没有"
  try {
    const c = await facade.automationDetail(name)
    card.value = (c.data?.automation ?? null) as Record<string, unknown> | null
  } catch (e) {
    cardError.value = String(e)
  }
})

// 三态：true＝本回合已派发（条件持续满足不重复）／false＝待条件重新成立／null＝暂无运行期事实
// ⛔ 不从 trigger_7d 或触发计数反推（计数是事实、派发是决策）
const edge = computed(() => {
  const v = card.value?.last_met
  if (v === true) return { type: 'success' as const, label: '本回合已派发', hint: '条件持续满足，不会重复派发' }
  if (v === false) return { type: 'default' as const, label: '待条件重新成立', hint: '上轮全条件未满足，等下一次跳变沿' }
  return { type: 'warning' as const, label: '暂无运行期事实', hint: '字段未就绪或该自动化还没跑过（不冒充 false）' }
})
const lastDispatched = computed(() => {
  const v = card.value?.last_dispatched_at
  return typeof v === 'string' && v ? v : ''
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

        <n-card title="运行期（触发沿）" :bordered="false" class="block">
          <n-space v-if="!cardError" align="center" :size="10">
            <n-tag :type="edge.type" :title="edge.hint" :bordered="false">{{ edge.label }}</n-tag>
            <n-text depth="3" style="font-size: 12px">{{ edge.hint }}</n-text>
          </n-space>
          <n-text v-if="lastDispatched" depth="3" style="font-size: 12px; display: block; margin-top: 6px">
            上次派发：{{ lastDispatched }}
          </n-text>
          <n-alert v-if="cardError" type="warning" :bordered="false" style="margin-top: 8px">
            运行期事实读不到：{{ cardError }}（⛔ 不等于「没有派发过」）
          </n-alert>
        </n-card>

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
