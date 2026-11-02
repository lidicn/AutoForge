<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NEmpty, NPopconfirm, NTag, NText, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { L2Action, PendingItem, PendingListResponse } from '@/types/api'

const data = ref<PendingListResponse | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref<string | null>(null)
const message = useMessage()
// DCD 矩阵行 2：批准回执里的 L2 摘要必须显式给人看——空数组＝明文占位，
// 字段缺失＝「未读到」（⛔ 两种都不许留白，留白＝没读出来）。
const receipt = ref<{ summary: string; l2: L2Action[] | null } | null>(null)

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
    const res = await facade.pendingApprove(op.op_id)
    const l2 = res.data?.l2_summary
    // 缺字段与空数组是两件事：前者＝后端没给（无法确认），后者＝本条确实不含 L2。
    receipt.value = { summary: op.summary || op.tool, l2: Array.isArray(l2) ? l2 : null }
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
    title: 'L2 动作',
    key: 'l2_summary',
    width: 260,
    render: (row) => {
      const l2 = row.l2_summary
      // ⛔ 只渲染后端给的字段，前端不算 L2；空数组＝显式占位，缺字段＝未读到。
      if (!Array.isArray(l2)) return h(NText, { depth: 3, style: 'font-size:12px' }, '未读到')
      if (l2.length === 0) return h(NText, { depth: 3, style: 'font-size:12px' }, '本条不含 L2 动作')
      return h(
        'div',
        { style: 'display:flex;flex-direction:column;gap:3px' },
        l2.map((it) =>
          h('div', { style: 'font-size:12px;line-height:1.5' }, [
            h('span', {}, '本条含：'),
            h('code', { style: 'background:#f2f4f8;border-radius:4px;padding:1px 4px' }, it.action),
            it.alias ? h('span', {}, `（${it.alias}）`) : null,
          ]),
        ),
      )
    },
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

    <n-card v-if="receipt" title="部署审批摘要" :bordered="false" class="block">
      <p class="receipt-sub">最近一次批准：{{ receipt.summary }}</p>
      <ul v-if="receipt.l2 && receipt.l2.length" class="l2-list">
        <li v-for="(it, i) in receipt.l2" :key="`${it.action}-${it.node}-${i}`" class="l2-item">
          <span class="l2-main">
            本条含：<code>{{ it.action }}</code><template v-if="it.alias">（{{ it.alias }}）</template>
          </span>
          <span v-if="it.automation || it.node" class="l2-meta">
            {{ it.automation }}<template v-if="it.node"> · 节点 {{ it.node }}</template>
          </span>
        </li>
      </ul>
      <p v-else-if="receipt.l2" class="l2-note l2-empty">本条不含 L2 动作</p>
      <p v-else class="l2-note l2-missing">
        未读到 L2 摘要：批准回执里没有 l2_summary 字段（后端未返回，本条是否含 L2 动作无法确认）
      </p>
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
.receipt-sub {
  margin: 0 0 10px;
  color: #8b93a7;
  font-size: 13px;
}
.l2-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.l2-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 10px;
  padding: 7px 0;
  border-top: 1px solid #f0f2f6;
}
.l2-item:first-child {
  border-top: 0;
}
.l2-main {
  font-size: 14px;
  color: #1f2937;
}
.l2-main code {
  background: #f2f4f8;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 13px;
}
.l2-meta {
  color: #8b93a7;
  font-size: 12px;
}
.l2-note {
  margin: 0;
  font-size: 14px;
}
.l2-empty {
  color: #8b93a7;
}
.l2-missing {
  color: #b45309;
}
</style>
