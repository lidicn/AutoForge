<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NAlert, NCard, NDataTable, NTag } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { FailureKind, FaultKind, FaultsResponse } from '@/types/api'

const data = ref<FaultsResponse | null>(null)
const loading = ref(true)
const error = ref('')

const kindColumns: DataTableColumns<FaultKind> = [
  { title: '值', key: 'value', width: 130, render: (row) => h('code', { style: 'font-size:12px' }, row.value) },
  { title: '标签', key: 'label', width: 190 },
  {
    title: '注入层',
    key: 'inject_layer',
    width: 120,
    render: (row) => h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => row.inject_layer }),
  },
  {
    title: '期望处理器',
    key: 'expected_handler',
    render: (row) => h('code', { style: 'font-size:12px' }, row.expected_handler),
  },
]

const failColumns: DataTableColumns<FailureKind> = [
  { title: '键', key: 'key', width: 130, render: (row) => h('code', { style: 'font-size:12px' }, row.key) },
  { title: '标签', key: 'label', width: 160 },
  { title: '触发边', key: 'edge', width: 220, render: (row) => h('code', { style: 'font-size:12px' }, row.edge) },
  {
    title: '可恢复',
    key: 'recoverable',
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.recoverable ? 'success' : 'error' },
        { default: () => (row.recoverable ? '✓ 可恢复' : '✗ 不可恢复') },
      ),
  },
]

onMounted(async () => {
  try {
    const res = await facade.faults()
    data.value = res.data
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
      <h1>故障注入图鉴</h1>
      <p class="sub">AutoForge G5 故障注入体系：五种故障类型 + 四类失败模式映射</p>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 16px">{{ error }}</n-alert>

    <n-card title="故障类型" :bordered="false" class="block">
      <n-data-table
        :columns="kindColumns"
        :data="data?.kinds ?? []"
        :loading="loading"
        :bordered="false"
        :row-key="(row: FaultKind) => row.value"
      />
    </n-card>

    <n-card title="失败模式" :bordered="false" class="block">
      <n-data-table
        :columns="failColumns"
        :data="data?.failures ?? []"
        :loading="loading"
        :bordered="false"
        :row-key="(row: FailureKind) => row.key"
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
</style>
