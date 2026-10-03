<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NInput, NPopconfirm, NStatistic, NTag, NText, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { InsightRecord, InsightsPendingResponse } from '@/types/api'

const data = ref<InsightsPendingResponse | null>(null)
const loading = ref(true)
const error = ref('')
const busy = ref<string | null>(null)
const showDecided = ref(false)
const rejectReason = ref('')
const message = useMessage()

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await facade.insightsPending(showDecided.value)
    data.value = res.data
  } catch (e) {
    // 读不到就明说读不到：把失败渲染成"没有提案"会让本视图自己造出假安心
    data.value = null
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

function onToggleDecided(v: boolean) {
  showDecided.value = v
  load()
}

/** 交接进待批队列。后端原话已写明"这一步还没有部署"，照抄给人看，不改写。 */
async function approve(rec: InsightRecord) {
  busy.value = rec.proposal_id
  try {
    const { data: res } = await facade.insightApprove(rec.proposal_id, 'webui')
    message.success(`已交接：${res.name} → 待批 ${res.pending ?? '（后端未回 op_id）'}`)
    message.info(res.note)
    await load()
  } catch (e) {
    message.error(`批准失败：${String(e)}`)
  } finally {
    busy.value = null
    rejectReason.value = ''
  }
}

async function reject(rec: InsightRecord) {
  busy.value = rec.proposal_id
  try {
    await facade.insightReject(rec.proposal_id, rejectReason.value || 'UI 人工拒绝', 'webui')
    message.info(`已拒绝并留痕：${rec.proposal_id}`)
    await load()
  } catch (e) {
    message.error(`拒绝失败：${String(e)}`)
  } finally {
    busy.value = null
    rejectReason.value = ''
  }
}

function fmtAt(epoch: number | null): string {
  if (epoch === null || !Number.isFinite(epoch)) return '—'
  const d = new Date(epoch * 1000)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { hour12: false })
}

const confCol: DataTableColumns<InsightRecord>[number] = {
  title: '置信',
  key: 'conf',
  width: 88,
  render: (row) => {
    // 契约表 `ma/insights` 里没有 `conf` 这一项。缺报时后端按 0.0 落 ask 档，但把"0.00"
    // 直接画出来会被读成"MA 说这条不值"——那是把对端的沉默读成对端的否定。缺报就照实写。
    const unreported = row.transport.conf_reported === false
    return h(
      NTag,
      { size: 'small', bordered: false, type: unreported ? 'default' : row.conf >= 0.7 ? 'warning' : 'default' },
      { default: () => (unreported ? '未上报' : row.conf.toFixed(2)) },
    )
  },
}

const irCol: DataTableColumns<InsightRecord>[number] = {
  title: '可交接',
  key: 'suggested_ir',
  width: 150,
  render: (row) => {
    // 后端②：只有自然语言、没有编译后 IR 的提案 approve 一律 400，服务端不代为造图
    if (!row.suggested_ir) {
      return h(NTag, { size: 'small', bordered: false, type: 'error' }, { default: () => '无 IR（不能批准）' })
    }
    return h(NTag, { size: 'small', bordered: false, type: 'success' }, { default: () => '含编译后 IR' })
  },
}

const timeCol = (title: string, key: 'received_at' | 'decided_at', width = 180) => ({
  title,
  key,
  width,
  render: (row: InsightRecord) => h(
    NText,
    { depth: row[key] ? 1 : 3, style: 'font-size:12px' },
    { default: () => fmtAt(row[key] ?? null) },
  ),
} as DataTableColumns<InsightRecord>[number])

/** 契约表里 AF 不消费、但批的人要看的依据：分类 / 当事人 / 房间 / 证据条数。 */
function peerMeta(row: InsightRecord): string {
  const t = row.transport
  const parts: string[] = []
  if (t.kind) parts.push(String(t.kind))
  const persons = Array.isArray(t.persons) ? t.persons : []
  if (persons.length) parts.push(`${persons.length} 人：${persons.slice(0, 3).join('、')}${persons.length > 3 ? '…' : ''}`)
  if (t.room) parts.push(String(t.room))
  if (typeof t.evidence_count === 'number') parts.push(`证据 ${t.evidence_count} 条`)
  return parts.join(' · ')
}

const pendingColumns: DataTableColumns<InsightRecord> = [
  {
    title: '洞察内容',
    key: 'natural_language',
    ellipsis: { tooltip: true },
    render: (row) => h('div', {}, [
      h('div', { style: 'font-weight:600' }, row.natural_language || '（空文本提案）'),
      h('code', { style: 'font-size:12px;color:#8b93a7' }, row.hypothesis_id),
      // 多人同框是现实：当事人不在 summary 里时，这里就是唯一能看到"涉及谁"的地方
      ...(peerMeta(row) ? [h('div', { style: 'font-size:12px;color:#8b93a7' }, peerMeta(row))] : []),
    ]),
  },
  {
    title: '来源',
    key: 'source',
    width: 90,
    render: (row) => h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => row.source }),
  },
  confCol,
  irCol,
  timeCol('收到', 'received_at', 180),
  {
    title: '操作',
    key: 'action',
    width: 200,
    render: (row) => h('div', { style: 'display:flex;gap:8px' }, [
      h(
        NPopconfirm,
        { onPositiveClick: () => approve(row) },
        {
          // 措辞即裁定的安全边界：approve ≠ 部署，仍需待批队列第二次批准
          default: () => h('div', { style: 'max-width:280px' },
            '批准 = 把这条洞察交接进「待批队列」，仍然不会部署；要落盘还得在待批队列里再批一次。确认交接？'),
          trigger: () => h(
            NButton,
            {
              size: 'small', type: 'primary', loading: busy.value === row.proposal_id,
              disabled: !row.suggested_ir,
            },
            { default: () => '交接（不部署）' },
          ),
        },
      ),
      h(
        NPopconfirm,
        { onPositiveClick: () => reject(row) },
        {
          default: () => h('div', { style: 'width:260px;display:flex;flex-direction:column;gap:8px' }, [
            h('div', {}, '拒绝也要留痕（归档进 decided，不产生任何待批操作）。'),
            h(NInput, {
              size: 'small', placeholder: '拒绝理由（可选）', value: rejectReason.value,
              'onUpdate:value': (v: string) => { rejectReason.value = v },
            }),
          ]),
          trigger: () => h(
            NButton,
            { size: 'small', loading: busy.value === row.proposal_id },
            { default: () => '拒绝' },
          ),
        },
      ),
    ]),
  },
]

const decidedColumns: DataTableColumns<InsightRecord> = [
  {
    title: '洞察内容',
    key: 'natural_language',
    ellipsis: { tooltip: true },
    render: (row) => h('div', {}, [
      h('div', {}, row.natural_language || '（空文本提案）'),
      h('code', { style: 'font-size:12px;color:#8b93a7' }, row.proposal_id),
    ]),
  },
  {
    title: '判定',
    key: 'status',
    width: 100,
    render: (row) => h(
      NTag,
      { size: 'small', bordered: false, type: row.status === 'approved' ? 'success' : 'warning' },
      { default: () => row.status },
    ),
  },
  { title: '判定人', key: 'decided_by', width: 130 },
  { title: '理由 / 待批把手', key: 'reason', ellipsis: { tooltip: true } },
  timeCol('判定时间', 'decided_at', 180),
]

const rows = computed(() => data.value?.proposals ?? [])
const decided = computed(() => (showDecided.value ? data.value?.decided ?? [] : []))
const stats = computed(() => data.value?.queue ?? null)
const empty = computed(() => data.value !== null && data.value.count === 0)

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>MA 洞察提案</h1>
      <p class="sub">
        ADM 联动 ④A：对端投来的洞察先<b>只落盘</b>——批准 = 交接进待批队列，<b>不是部署</b>；拒绝也留痕
      </p>
    </div>

    <n-alert v-if="error" type="error" title="队列读取失败" class="block">
      {{ error }}
      <n-button size="small" style="margin-left: 12px" @click="load">重试</n-button>
    </n-alert>

    <n-alert v-else-if="empty" type="warning" title="pending 目录里没有提案" class="block">
      这<b>不等于</b>"MA 没投过"，也不等于"联动链路健康"。同一个空态有四种成因：桥未上线、
      没订到 <code>ma/insights</code>、投来的都被裁定过、以及本视图读的目录与桥写的目录不一致。
      下面"队列落点"就是判据——路径不对就先查 <code>--store-root</code> 口径，别急着相信"没有提案"。
    </n-alert>

    <n-card v-if="stats" title="队列账目" :bordered="false" class="block">
      <div class="stat-grid">
        <n-statistic label="待裁定（pending）" :value="stats.pending" />
        <n-statistic label="已裁定（decided）" :value="stats.decided" />
        <n-statistic label="队列上限" :value="stats.limit" />
        <n-statistic label="读不出来的文件" :value="stats.unreadable.length" />
      </div>
      <n-text depth="3" style="font-size:12px">队列落点：<code>{{ stats.root }}</code></n-text>
      <n-alert
        v-if="stats.unreadable.length"
        type="error"
        :title="`${stats.unreadable.length} 个提案文件读不出来（记账，不静默丢弃）`"
        class="hint"
      >
        <ul class="raw">
          <li v-for="u in stats.unreadable" :key="u"><code>{{ u }}</code></li>
        </ul>
      </n-alert>
      <div class="toolbar">
        <n-button size="small" :disabled="loading" @click="load">{{ loading ? '读取中…' : '刷新' }}</n-button>
        <n-button size="small" :type="showDecided ? 'primary' : 'default'" @click="onToggleDecided(!showDecided)">
          {{ showDecided ? '收起已裁定' : '展开已裁定' }}
        </n-button>
      </div>
    </n-card>

    <n-card title="待裁定的洞察" :bordered="false" class="block">
      <n-data-table
        :columns="pendingColumns"
        :data="rows"
        :loading="loading"
        size="small"
        :pagination="{ pageSize: 10 }"
        :row-key="(row: InsightRecord) => row.proposal_id"
      />
    </n-card>

    <n-card v-if="showDecided" title="已裁定（归档）" :bordered="false" class="block">
      <n-data-table
        :columns="decidedColumns"
        :data="decided"
        :loading="loading"
        size="small"
        :pagination="{ pageSize: 10 }"
        :row-key="(row: InsightRecord) => row.proposal_id"
      />
    </n-card>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 20px;
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
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}
.hint {
  margin: 14px 0;
}
.toolbar {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.raw {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
}
</style>
