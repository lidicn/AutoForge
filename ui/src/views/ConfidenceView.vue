<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NDataTable, NInput, NProgress, NTag, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { ConfResponse, ConfidenceItem } from '@/types/api'

const data = ref<ConfResponse | null>(null)
const loading = ref(true)
const error = ref('')
// 与 AutomationsListView 统一口径：`_all` 表示聚合全部归档（API_CONTRACT 第 6 项）
const graphName = ref('_all')
const message = useMessage()

const BAND_TYPE: Record<string, 'success' | 'warning' | 'error'> = {
  auto: 'success',
  shadow: 'warning',
  ask: 'error',
}
const BAND_LABEL: Record<string, string> = {
  auto: '自动部署',
  shadow: 'Shadow 只读',
  ask: 'Ask 需审批',
}
const BAND_COLOR: Record<string, string> = {
  auto: '#10b981',
  shadow: '#f59e0b',
  ask: '#dc2626',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await facade.conf(graphName.value)
    data.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function intervene(automation_id: string) {
  try {
    const res = await facade.intervene(graphName.value, automation_id)
    data.value = res.data
    message.success(`已模拟人工干预：${automation_id}`)
  } catch (e) {
    error.value = String(e)
    message.error('干预失败')
  }
}

const columns: DataTableColumns<ConfidenceItem> = [
  {
    title: 'Automation',
    key: 'automation_id',
    render: (row) => h('code', { style: 'font-size:12px' }, row.automation_id),
  },
  {
    title: '置信度',
    key: 'confidence',
    width: 100,
    render: (row) => h('span', { style: 'font-weight:600' }, row.confidence.toFixed(2)),
  },
  {
    title: 'Band',
    key: 'band',
    width: 200,
    render: (row) =>
      h(
        NTag,
        { type: BAND_TYPE[row.band] ?? 'default', size: 'small', bordered: false, round: true },
        { default: () => `${row.band} — ${BAND_LABEL[row.band] ?? row.band}` },
      ),
  },
  {
    title: '进度',
    key: 'progress',
    width: 200,
    render: (row) =>
      h(NProgress, {
        type: 'line',
        percentage: Math.round(row.confidence * 100),
        color: BAND_COLOR[row.band],
        railColor: '#f1f2f5',
        height: 8,
        showIndicator: false,
      }),
  },
  {
    title: '操作',
    key: 'action',
    width: 150,
    render: (row) =>
      h(
        NButton,
        { size: 'small', onClick: () => intervene(row.automation_id) },
        { default: () => '模拟人工干预' },
      ),
  },
]

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div class="head-row">
        <div>
          <h1>置信度面板</h1>
          <p class="sub">依据置信度分档：ask / shadow / auto</p>
        </div>
        <n-input
          v-model:value="graphName"
          placeholder="Graph 名称（_all=全部）"
          style="max-width: 260px"
          @blur="load"
          @keyup.enter="load"
        />
      </div>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 16px">{{ error }}</n-alert>

    <template v-if="data">
      <n-card title="阈值参考" :bordered="false" class="block">
        <div class="threshold-bar">
          <div class="zone ask" :style="{ width: data.thresholds.shadow_low * 100 + '%' }">
            <span>ask &lt; {{ data.thresholds.shadow_low }}</span>
          </div>
          <div
            class="zone shadow"
            :style="{ width: (data.thresholds.auto - data.thresholds.shadow_low) * 100 + '%' }"
          >
            <span>shadow ≥ {{ data.thresholds.shadow_low }}</span>
          </div>
          <div class="zone auto" :style="{ width: (1 - data.thresholds.auto) * 100 + '%' }">
            <span>auto ≥ {{ data.thresholds.auto }}</span>
          </div>
        </div>
        <div class="scale"><span>0</span><span>1.0</span></div>
      </n-card>

      <n-card title="自动化置信度" :bordered="false" class="block">
        <n-data-table
          :columns="columns"
          :data="data.items"
          :loading="loading"
          :bordered="false"
          :row-key="(row: ConfidenceItem) => row.automation_id"
        />
      </n-card>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 20px;
}
.head-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.page-head h1 {
  margin: 0 0 4px;
  font-size: 26px;
}
.page-head .sub {
  margin: 0;
  color: #8b93a7;
  font-size: 14px;
}
.block {
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.threshold-bar {
  display: flex;
  height: 36px;
  border-radius: 8px;
  overflow: hidden;
}
.zone {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
}
.zone.ask {
  background: #fee2e2;
  color: #991b1b;
}
.zone.shadow {
  background: #fef3c7;
  color: #92400e;
}
.zone.auto {
  background: #d1fae5;
  color: #065f46;
}
.scale {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #a3aab8;
  margin-top: 6px;
}
</style>
