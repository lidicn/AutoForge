<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NCard, NDataTable, NInput, NTag, NSpace, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { ConfidenceItem, GraphItem } from '@/types/api'

const message = useMessage()

const items = ref<GraphItem[]>([])
const confidences = ref<Map<string, ConfidenceItem>>(new Map())
const loading = ref(true)
const filter = ref('')
const tagFilter = ref('')
const error = ref('')

// ── v0.6.0 批量启停工具条 ──
const bulkTag = ref('')
const bulkBusy = ref(false)

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

function getConf(item: GraphItem): ConfidenceItem | undefined {
  for (const id of item.automation_ids ?? []) {
    const found = confidences.value.get(id)
    if (found) return found
  }
  return confidences.value.get(item.name)
}

const filtered = computed(() => {
  let list = items.value
  const q = filter.value.toLowerCase()
  if (q) {
    list = list.filter(
      (i) =>
        i.name.toLowerCase().includes(q) ||
        (i.note ?? '').toLowerCase().includes(q) ||
        i.mode.toLowerCase().includes(q),
    )
  }
  const t = tagFilter.value.trim().toLowerCase()
  if (t) {
    list = list.filter((i) => (i.tags ?? []).some((tag) => tag.toLowerCase().includes(t)))
  }
  return list
})

const columns: DataTableColumns<GraphItem> = [
  {
    title: 'ID / 备注',
    key: 'name',
    render: (row) =>
      h('div', [
        h('div', { style: 'font-weight:600;font-family:monospace' }, row.name),
        h('div', { style: 'font-size:12px;color:#8b93a7' }, row.note),
      ]),
  },
  {
    title: 'mode',
    key: 'mode',
    width: 110,
    render: (row) => h(NTag, { size: 'small', bordered: false }, { default: () => row.mode }),
  },
  {
    title: '置信度',
    key: 'confidence',
    width: 190,
    render: (row) => {
      const c = getConf(row)
      if (!c) return h('span', { style: 'color:#c0c4cc' }, '—')
      return h(
        NTag,
        { type: BAND_TYPE[c.band] ?? 'default', size: 'small', bordered: false, round: true },
        { default: () => `${BAND_LABEL[c.band] ?? c.band} (${c.confidence.toFixed(2)})` },
      )
    },
  },
  {
    title: '版本',
    key: 'latest_version',
    width: 80,
    render: (row) => h('span', `v${row.latest_version}`),
  },
  {
    title: '保存时间',
    key: 'saved_at',
    width: 120,
    render: (row) => h('span', { style: 'color:#8b93a7;font-size:13px' }, row.saved_at?.slice(0, 10)),
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row) =>
      h(
        RouterLink,
        { to: `/automations/${row.name}`, style: 'text-decoration:none' },
        { default: () => h(NButton, { size: 'small', quaternary: true, type: 'primary' }, { default: () => '详情' }) },
      ),
  },
]

async function bulkToggle(enable: boolean) {
  const tag = bulkTag.value.trim()
  if (!tag) {
    message.warning('请先填写要操作的标签')
    return
  }
  bulkBusy.value = true
  try {
    const res = await facade.enableByTag(tag, enable)
    if (res.data.ok) {
      const n = (res.data.affected ?? []).length
      message.success(`已${enable ? '启用' : '禁用'}标签「${tag}」下的 ${n} 个归档`)
    } else {
      message.error('批量操作失败')
    }
  } catch (e) {
    message.error('批量操作失败：' + String(e))
  } finally {
    bulkBusy.value = false
  }
}

onMounted(async () => {
  try {
    const [gRes, cRes] = await Promise.all([facade.graphs(), facade.conf('_all')])
    items.value = gRes.data.items
    if (cRes.data.items) {
      const m = new Map<string, ConfidenceItem>()
      for (const ci of cRes.data.items) m.set(ci.automation_id, ci)
      confidences.value = m
    }
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>自动化列表</h1>
      <p class="sub">共 {{ items.length }} 个归档用例</p>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 16px">
      {{ error }}
    </n-alert>

    <n-card :bordered="false" class="list-card">
      <n-space align="center" style="margin-bottom: 16px">
        <n-input
          v-model:value="filter"
          placeholder="筛选 ID / 备注 / mode..."
          clearable
          style="max-width: 280px"
        />
        <n-input
          v-model:value="tagFilter"
          placeholder="按标签筛选（如 prod）"
          clearable
          style="max-width: 200px"
        />
      </n-space>

      <n-space align="center" style="margin-bottom: 16px">
        <n-text depth="3" style="font-size: 13px">批量启停：</n-text>
        <n-input
          v-model:value="bulkTag"
          placeholder="标签名，如 prod"
          clearable
          style="max-width: 180px"
        />
        <n-button
          type="primary"
          :loading="bulkBusy"
          :disabled="bulkBusy"
          @click="() => bulkToggle(true)"
        >
          启用该标签全部
        </n-button>
        <n-button
          type="warning"
          :loading="bulkBusy"
          :disabled="bulkBusy"
          @click="() => bulkToggle(false)"
        >
          禁用该标签全部
        </n-button>
      </n-space>

      <n-data-table
        :columns="columns"
        :data="filtered"
        :loading="loading"
        :bordered="false"
        :row-key="(row: GraphItem) => row.name"
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
.list-card {
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
</style>
