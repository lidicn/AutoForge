<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NSelect, NTag } from 'naive-ui'
import { facade } from '@/api'
import type { DiffResponse, GraphItem } from '@/types/api'

const graphs = ref<GraphItem[]>([])
const selectedGraph = ref('')
const selectedOld = ref('1')
const selectedNew = ref('2')
const diffData = ref<DiffResponse | null>(null)
const diffLoading = ref(false)
const error = ref('')
// 归档列表加载失败与对比失败必须分开：此前两者写同一个 `error`，
// 于是"取列表 403"会被渲染成「对比失败」，把人指向错的地方。
const loadError = ref('')

const versions = ref<number[]>([])
const latestVersion = ref(1)

const graphOptions = computed(() => graphs.value.map((g) => ({ label: g.name, value: g.name })))
const versionOptions = computed(() => versions.value.map((v) => ({ label: `v${v}`, value: String(v) })))

function syncVersions() {
  const g = graphs.value.find((x) => x.name === selectedGraph.value)
  latestVersion.value = g ? g.latest_version : 1
  versions.value = Array.from({ length: latestVersion.value }, (_, i) => i + 1)
  selectedOld.value = '1'
  selectedNew.value = String(latestVersion.value)
}

watch(selectedGraph, syncVersions)

onMounted(async () => {
  try {
    const res = await facade.graphs()
    graphs.value = res.data.items
    if (graphs.value.length) {
      selectedGraph.value = graphs.value[0].name
      syncVersions()
    }
  } catch (e) {
    loadError.value = `读取归档列表失败：${e}`
  }
})

async function runDiff() {
  diffLoading.value = true
  error.value = ''
  try {
    const res = await facade.diff(selectedGraph.value, selectedOld.value, selectedNew.value)
    diffData.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    diffLoading.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <h1>版本与 Diff</h1>
      <p class="sub">对比同一归档在不同版本间的结构化变更</p>
    </div>

    <n-card title="选择版本" :bordered="false" class="block">
      <div class="controls">
        <div class="field">
          <span class="label">Graph</span>
          <n-select v-model:value="selectedGraph" :options="graphOptions" style="width: 220px" />
        </div>
        <div class="field">
          <span class="label">旧版</span>
          <n-select v-model:value="selectedOld" :options="versionOptions" style="width: 110px" />
        </div>
        <div class="field">
          <span class="label">新版</span>
          <n-select v-model:value="selectedNew" :options="versionOptions" style="width: 110px" />
        </div>
        <n-button type="primary" :loading="diffLoading" :disabled="latestVersion < 2" @click="runDiff">
          对比
        </n-button>
      </div>
      <p v-if="latestVersion < 2" class="hint">
        该归档目前只有 1 个版本，暂无可对比项（可用 <code>forge store save</code> 生成第二个版本）。
      </p>
    </n-card>

    <n-alert v-if="loadError" type="error" title="加载失败" style="margin-bottom: 16px">{{ loadError }}</n-alert>
    <n-alert v-if="error" type="error" title="对比失败" style="margin-bottom: 16px">{{ error }}</n-alert>

    <template v-if="diffData">
      <n-card title="Diff 渲染" :bordered="false" class="block">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap">
          <n-tag size="small" :bordered="false">
            v{{ diffData.old }} 备注：{{ diffData.notes?.old || '（空）' }}
          </n-tag>
          <span style="color: #c0c4cc">→</span>
          <n-tag size="small" :bordered="false">
            v{{ diffData.new }} 备注：{{ diffData.notes?.new || '（空）' }}
          </n-tag>
        </div>
        <pre class="diff-block">{{ diffData.render }}</pre>
        <p
          v-if="diffData.render === '无差异'"
          style="margin: 8px 0 0; font-size: 13px; color: #8b93a7"
        >
          两版本的「图内容」（节点 / 边 / 参数 / 元信息）完全一致；归档备注不参与比对，仅备注不同属正常。
        </p>
      </n-card>

      <n-card title="结构化 Diff" :bordered="false" class="block">
        <div class="diff-grid">
          <div v-if="diffData.structured.added_nodes.length" class="diff-section">
            <div class="sec-title added">+ 新增节点 ({{ diffData.structured.added_nodes.length }})</div>
            <ul>
              <li v-for="n in diffData.structured.added_nodes" :key="n.node_id">
                <code>{{ n.node_id }}</code> {{ n.node }}
              </li>
            </ul>
          </div>
          <div v-if="diffData.structured.removed_nodes.length" class="diff-section">
            <div class="sec-title removed">− 移除节点 ({{ diffData.structured.removed_nodes.length }})</div>
            <ul>
              <li v-for="n in diffData.structured.removed_nodes" :key="n.node_id">
                <code>{{ n.node_id }}</code> {{ n.node }}
              </li>
            </ul>
          </div>
          <div v-if="diffData.structured.added_edges.length" class="diff-section">
            <div class="sec-title added">+ 新增边 ({{ diffData.structured.added_edges.length }})</div>
            <ul>
              <li v-for="e in diffData.structured.added_edges" :key="e.from + e.to + e.kind">
                <code>{{ e.from }} → {{ e.to }}</code> {{ e.kind }}
              </li>
            </ul>
          </div>
          <div v-if="diffData.structured.removed_edges.length" class="diff-section">
            <div class="sec-title removed">− 移除边 ({{ diffData.structured.removed_edges.length }})</div>
            <ul>
              <li v-for="e in diffData.structured.removed_edges" :key="e.from + e.to + e.kind">
                <code>{{ e.from }} → {{ e.to }}</code> {{ e.kind }}
              </li>
            </ul>
          </div>
          <div v-if="diffData.structured.meta_changes.length" class="diff-section">
            <div class="sec-title">◆ 元数据变更 ({{ diffData.structured.meta_changes.length }})</div>
            <ul>
              <li v-for="m in diffData.structured.meta_changes" :key="m.key">
                <code>{{ m.key }}</code>: {{ m.old_value }} → {{ m.new_value }}
              </li>
            </ul>
          </div>
          <div
            v-if="
              !diffData.structured.added_nodes.length &&
              !diffData.structured.removed_nodes.length &&
              !diffData.structured.added_edges.length &&
              !diffData.structured.removed_edges.length &&
              !diffData.structured.meta_changes.length
            "
            class="no-diff"
          >
            <n-tag type="success" :bordered="false" round size="large">两个版本完全一致</n-tag>
          </div>
        </div>
      </n-card>
    </template>
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
.controls {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.label {
  font-size: 12.5px;
  font-weight: 600;
  color: #6b7280;
}
.hint {
  margin: 14px 0 0;
  font-size: 13px;
  color: #8b93a7;
}
.diff-block {
  margin: 0;
  background: #1e1e2e;
  color: #d4d4d4;
  padding: 16px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
}
.diff-grid {
  display: grid;
  gap: 12px;
}
.diff-section {
  padding: 14px;
  background: #f8f9fc;
  border-radius: 8px;
}
.sec-title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
}
.added {
  color: #10b981;
}
.removed {
  color: #dc2626;
}
ul {
  margin: 0;
  padding-left: 20px;
}
li {
  font-size: 13px;
  margin-bottom: 4px;
}
code {
  font-family: monospace;
  font-size: 12px;
  background: #eef0f5;
  padding: 1px 5px;
  border-radius: 4px;
}
.no-diff {
  padding: 20px;
  text-align: center;
}
</style>
