<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NDataTable, NStatistic, NTag, NText } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { EvidenceAutomation, EvidenceProdResponse } from '@/types/api'

const data = ref<EvidenceProdResponse | null>(null)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await facade.evidenceProd()
    data.value = res.data
  } catch (e) {
    // 读不到就明说读不到：把失败渲染成"零证据"会让视图自己造出假安心
    data.value = null
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

function fmtAt(epoch: number | null): string {
  if (epoch === null || !Number.isFinite(epoch)) return '—'
  const d = new Date(epoch * 1000)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', { hour12: false })
}

const countCol = (key: keyof EvidenceAutomation, tone: 'success' | 'warning' | 'error' | undefined) => ({
  title: '',
  key,
  width: 96,
  render: (row: EvidenceAutomation) => {
    const n = row[key] as number
    if (!n) return h(NText, { depth: 3 }, { default: () => '0' })
    return h(
      NTag,
      { size: 'small', bordered: false, type: tone ?? 'info' },
      { default: () => String(n) },
    )
  },
})

const columns: DataTableColumns<EvidenceAutomation> = [
  {
    title: '自动化',
    key: 'automation_id',
    ellipsis: { tooltip: true },
    render: (row) => h('code', { style: 'font-size:12px' }, row.automation_id),
  },
  { ...countCol('verified_in_prod', 'success'), title: '验过' },
  { ...countCol('failed_in_prod', 'error'), title: '验出问题' },
  { ...countCol('unmodeled_in_prod', 'warning'), title: '无从验证' },
  { ...countCol('shadow', undefined), title: '影子' },
  { ...countCol('canary', undefined), title: '金丝雀' },
  { ...countCol('conflict', undefined), title: '冲突' },
  {
    title: '最近一次验过',
    key: 'last_verified_at',
    width: 190,
    render: (row) => h(NText, { depth: row.last_verified_at ? 1 : 3 }, { default: () => fmtAt(row.last_verified_at) }),
  },
  {
    title: '最近一次验出问题',
    key: 'last_failed_at',
    width: 190,
    render: (row) => h(NText, { depth: row.last_failed_at ? 1 : 3 }, { default: () => fmtAt(row.last_failed_at) }),
  },
]

const summary = computed(() => data.value?.summary ?? null)
const rows = computed(() => data.value?.automations ?? [])
const empty = computed(() => data.value !== null && data.value.summary.tracked_automations === 0)

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>生产态证据</h1>
      <p class="sub">
        可信闭环 F4 ③：每个自动化在真实环境里被验证过多少次——verified / failed / unmodeled 三档并列，不合并成"看起来没问题"
      </p>
    </div>

    <n-alert v-if="error" type="error" title="证据读取失败" class="block">
      {{ error }}
      <n-button size="small" style="margin-left: 12px" @click="load">重试</n-button>
    </n-alert>

    <n-alert v-else-if="empty" type="warning" title="当前进程没有任何生产态验证事件" class="block">
      这<b>不等于</b>自动化被验证过。聚合器是进程内内存（af_watch），服务重启即清零。
      证据只有三个来源：影子对比（af_shadow）、金丝雀观察结论（af_executor 里<b>带 canary 节点</b>的真机下发）、
      写冲突仲裁（af_conflict_runtime）。<b>不带 canary 的普通真机下发不产生生产态证据</b>——跑一条再刷新是看不到的。
    </n-alert>

    <n-card v-if="summary" title="全局汇总" :bordered="false" class="block">
      <div class="stat-grid">
        <n-statistic label="验过（verified）" :value="summary.total_verified_in_prod" />
        <n-statistic label="验出问题（failed）" :value="summary.total_failed_in_prod" />
        <n-statistic label="无从验证（unmodeled）" :value="summary.total_unmodeled_in_prod" />
        <n-statistic label="冲突仲裁事件" :value="summary.total_conflict" />
        <n-statistic label="有证据的自动化" :value="summary.tracked_automations" />
        <n-statistic label="其中验出过问题" :value="summary.automations_with_failed" />
      </div>
      <n-alert
        v-if="summary.evicted_automations"
        type="info"
        :title="`${summary.evicted_automations} 个自动化的证据桶已被上限淘汰`"
        class="hint"
      >
        聚合器对每个自动化的事件条数与跟踪总数都有上限（只拦长跑与改名残留的空桶）。淘汰只会让本视图<b>少报</b>证据，不会凭空造出证据。
      </n-alert>
      <n-button size="small" :disabled="loading" @click="load">{{ loading ? '读取中…' : '刷新' }}</n-button>
    </n-card>

    <n-card title="按自动化" :bordered="false" class="block">
      <n-data-table
        :columns="columns"
        :data="rows"
        :loading="loading"
        size="small"
        :pagination="{ pageSize: 20 }"
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
  margin-bottom: 14px;
}
</style>
