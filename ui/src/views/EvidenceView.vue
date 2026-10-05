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
      <!-- 生产态三档并列：verified / failed / unmodeled 同栏出现，不合并成"看起来没问题" -->
      <div class="trio">
        <div class="tier tier-ok">
          <div class="tier-label">验过 · verified</div>
          <div class="tier-num">{{ summary.total_verified_in_prod }}</div>
          <div class="tier-sub">真实 HA 回放 / 金丝雀观察结论命中</div>
        </div>
        <div class="tier tier-bad">
          <div class="tier-label">验出问题 · failed</div>
          <div class="tier-num">{{ summary.total_failed_in_prod }}</div>
          <div class="tier-sub">真实环境与期望态不一致</div>
        </div>
        <div class="tier tier-warn">
          <div class="tier-label">无从验证 · unmodeled</div>
          <div class="tier-num">{{ summary.total_unmodeled_in_prod }}</div>
          <div class="tier-sub">带 canary 的真机下发里没建出可对照模型</div>
        </div>
      </div>

      <div class="stat-grid">
        <n-statistic label="冲突仲裁事件" :value="summary.total_conflict" />
        <n-statistic label="有证据的自动化" :value="summary.tracked_automations" />
        <n-statistic label="其中验出过问题" :value="summary.automations_with_failed" />
      </div>

      <n-alert
        :type="summary.evicted_automations ? 'warning' : 'success'"
        :title="summary.evicted_automations ? `证据桶淘汰 ${summary.evicted_automations} 个` : '证据桶淘汰 0 个'"
        class="hint"
      >
        <span v-if="summary.evicted_automations">
          聚合器对跟踪总数有上限（只拦长跑与改名残留的空桶）。淘汰只会让本视图<b>少报</b>证据，
          <b>不得声称"证据完整"</b>——被淘汰的自动化可能仍有生产态事件未被计入。
        </span>
        <span v-else>跟踪上限内暂无淘汰，证据未因容量被丢弃。</span>
      </n-alert>

      <n-alert type="warning" class="hint">
        <b>诚实铁律 #5：EXEMPT ≠ VERIFIED。</b>
        本视图的"验过"只来自真实 HA 回放 + 金丝雀观察结论 + 写冲突仲裁；
        shadow 的 <b>EXEMPT（人工豁免）</b>走人审通道、<b>不计入</b>此处 verified。
        没证据 ≠ 验证过，豁免 ≠ 通过。
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
.trio {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.tier {
  border-radius: 10px;
  padding: 14px 16px;
  border: 1px solid rgba(15, 23, 42, 0.08);
}
.tier-ok {
  background: rgba(63, 185, 80, 0.08);
  border-color: rgba(63, 185, 80, 0.35);
}
.tier-bad {
  background: rgba(224, 62, 62, 0.08);
  border-color: rgba(224, 62, 62, 0.35);
}
.tier-warn {
  background: rgba(240, 173, 78, 0.1);
  border-color: rgba(240, 173, 78, 0.4);
}
.tier-label {
  font-size: 13px;
  font-weight: 600;
  color: #4b5563;
}
.tier-num {
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
  margin: 4px 0;
}
.tier-ok .tier-num {
  color: #2f9e44;
}
.tier-bad .tier-num {
  color: #e03131;
}
.tier-warn .tier-num {
  color: #d9480f;
}
.tier-sub {
  font-size: 12px;
  color: #8b93a7;
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
