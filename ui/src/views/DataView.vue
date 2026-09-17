<script setup lang="ts">
import { ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NSelect,
  NStatistic,
  NUpload,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'
import { facade } from '@/api'
import type { StoreExportResponse, StoreImportResponse } from '@/types/api'

const message = useMessage()

const exporting = ref(false)
const importing = ref(false)
const lastExport = ref<StoreExportResponse | null>(null)
const lastImport = ref<StoreImportResponse | null>(null)
const strategy = ref<'skip' | 'overwrite' | 'rename'>('skip')

const strategyOptions = [
  { label: '跳过已存在 (skip)', value: 'skip' },
  { label: '覆盖 (overwrite)', value: 'overwrite' },
  { label: '重命名 (rename)', value: 'rename' },
]

async function exportStore() {
  exporting.value = true
  lastExport.value = null
  try {
    const res = await facade.storeExport()
    lastExport.value = res.data
    const blob = new Blob([JSON.stringify(res.data.bundle, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `autoforge-store-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
    message.success('已导出并开始下载')
  } catch (e) {
    message.error('导出失败：' + String(e))
  } finally {
    exporting.value = false
  }
}

async function importStore(options: UploadCustomRequestOptions) {
  const file = options.file.file as File
  importing.value = true
  lastImport.value = null
  try {
    const text = await file.text()
    const bundle = JSON.parse(text)
    const res = await facade.storeImport(bundle, strategy.value)
    lastImport.value = res.data
    if (res.data.ok) {
      const r = res.data
      message.success(
        `导入完成：新增 ${r.imported ?? 0}，跳过 ${r.skipped ?? 0}，重命名 ${r.renamed ?? 0}`,
      )
    } else {
      message.error(res.data.error ?? '导入失败')
    }
    options.onFinish()
  } catch (e) {
    message.error('导入失败：' + String(e))
    options.onError()
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <h1>数据管理</h1>
      <p class="sub">v0.7.0 模板导出与备份恢复 · 整库归档 bundle 搬运</p>
    </div>

    <n-card title="导出整库" :bordered="false" class="block">
      <p class="desc">将全部归档（含标签与最新版本）导出为单个 bundle JSON，用于备份或迁移到另一实例。</p>
      <n-button type="primary" :loading="exporting" @click="exportStore">导出并下载</n-button>
      <n-space v-if="lastExport" align="center" style="margin-top: 14px">
        <n-statistic label="归档数" :value="lastExport.names.length" />
        <n-statistic label="校验和" :value="lastExport.checksum?.slice(0, 8) ?? '—'" />
        <n-text depth="3" style="font-size: 13px">导出时间 {{ lastExport.exported_at }}</n-text>
      </n-space>
    </n-card>

    <n-card title="导入 bundle" :bordered="false" class="block">
      <p class="desc">选择此前导出的 bundle JSON 导入。冲突策略决定遇到同名归档时的行为。</p>
      <n-space align="center">
        <n-select v-model:value="strategy" :options="strategyOptions" style="width: 220px" />
        <n-upload
          accept=".json,application/json"
          :show-file-list="false"
          :custom-request="importStore"
          :disabled="importing"
        >
          <n-button type="primary" :loading="importing">选择文件并导入</n-button>
        </n-upload>
      </n-space>
      <n-alert
        v-if="lastImport"
        style="margin-top: 14px"
        :type="lastImport.ok ? 'success' : 'error'"
        :title="lastImport.ok ? '导入完成' : '导入失败'"
      >
        <template v-if="lastImport.ok">
          新增 {{ lastImport.imported ?? 0 }} · 跳过 {{ lastImport.skipped ?? 0 }} · 重命名
          {{ lastImport.renamed ?? 0 }} · 错误 {{ lastImport.errors ?? 0 }}
          <span v-if="lastImport.blast_radius">
            （影响 {{ lastImport.blast_radius.affected }} / 上限 {{ lastImport.blast_radius.limit }}）
          </span>
        </template>
        <template v-else>{{ lastImport.error }}</template>
      </n-alert>
    </n-card>

    <n-alert type="warning" title="爆炸半径提示" style="margin-top: 16px">
      导入是批量动作，会一次性写入多个归档。若 bundle 过大（超过 AUTOFORGE_BLAST_RADIUS 默认 8 条自动化），服务端会拒绝并要求显式放行。
    </n-alert>
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
.desc {
  font-size: 13px;
  color: #6b7280;
  margin: 0 0 12px;
  line-height: 1.7;
}
</style>
