<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NEmpty, NPopconfirm, NTag, NText, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { PendingItem, PendingListResponse } from '@/types/api'

const data = ref<PendingListResponse | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref<string | null>(null)
const message = useMessage()

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await facade.pendingList()
    data.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function approve(op: PendingItem) {
  busy.value = op.op_id
  try {
    await facade.pendingApprove(op.op_id)
    message.success(`已批准并回放落盘：${op.summary}`)
    await load()
  } catch (e) {
    message.error(`批准失败：${String(e)}`)
  } finally {
    busy.value = null
  }
}

async function reject(op: PendingItem) {
  busy.value = op.op_id
  try {
    await facade.pendingReject(op.op_id, 'UI 人工拒绝')
    message.info(`已拒绝：${op.summary}`)
    await load()
  } catch (e) {
    message.error(`拒绝失败：${String(e)}`)
  } finally {
    busy.value = null
  }
}

const columns: DataTableColumns<PendingItem> = [
  {
    title: '摘要',
    key: 'summary',
    render: (row) => h('div', { style: 'font-weight:600' }, row.summary || row.tool),
  },
  {
    title: '工具',
    key: 'tool',
    width: 140,
    render: (row) => h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => row.tool }),
  },
  {
    title: '提交者',
    key: 'submitted_by',
    width: 130,
    render: (row) => h('code', { style: 'font-size:12px' }, row.submitted_by || 'anonymous'),
  },
  {
    title: '爆炸半径',
    key: 'blast_radius',
    width: 120,
    render: (row) => {
      const br = row.blast_radius || {}
      const a = br.affected
      const lim = br.limit
      if (a == null) return h(NText, { depth: 3 }, { default: () => '—' })
      const danger = lim != null && a > lim
      return h(
        NTag,
        { size: 'small', bordered: false, type: danger ? 'error' : 'warning' },
        { default: () => `影响 ${a}${lim != null ? ` / 限 ${lim}` : ''}` },
      )
    },
  },
  {
    title: '提交时间',
    key: 'submitted_at',
    width: 180,
    render: (row) => h(NText, { depth: 3, style: 'font-size:12px' }, { default: () => row.submitted_at }),
  },
  {
    title: '操作',
    key: 'action',
    width: 170,
    render: (row) =>
      h('div', { style: 'display:flex;gap:8px' }, [
        h(
          NPopconfirm,
          { onPositiveClick: () => approve(row) },
          {
            default: () => '确认批准并回放落盘？',
            trigger: () =>
              h(
                NButton,
                { size: 'small', type: 'primary', loading: busy.value === row.op_id },
                { default: () => '批准' },
              ),
          },
        ),
        h(
          NPopconfirm,
          { onPositiveClick: () => reject(row) },
          {
            default: () => '确认拒绝该待批操作？',
            trigger: () =>
              h(
                NButton,
                { size: 'small', quaternary: true, type: 'error' },
                { default: () => '拒绝' },
              ),
          },
        ),
      ]),
  },
]

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div class="head-row">
        <div>
          <h1>待批队列</h1>
          <p class="sub">Agent 经 MCP 写入的全部落此处，UI 是唯一批准入口（MCP 不注册 approve）</p>
        </div>
        <n-button :loading="loading" secondary @click="load">刷新</n-button>
      </div>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 16px">{{ error }}</n-alert>

    <n-card title="待审批写操作" :bordered="false" class="block">
      <n-empty v-if="data && data.items.length === 0" description="暂无待批操作" />
      <n-data-table
        v-else
        :columns="columns"
        :data="data?.items ?? []"
        :loading="loading"
        :bordered="false"
        :row-key="(row: PendingItem) => row.op_id"
      />
    </n-card>
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
</style>
