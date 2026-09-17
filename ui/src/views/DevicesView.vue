<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NGi,
  NGrid,
  NInput,
  NList,
  NListItem,
  NSelect,
  NStatistic,
  NTabPane,
  NTabs,
  NTag,
  NThing,
  NProgress,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type {
  AliasesResponse,
  CatalogEntity,
  CatalogSnapshot,
  EntityStateResponse,
  ResolveMetricsResponse,
  ResolveResponse,
} from '@/types/api'

const message = useMessage()

// ── 概览 ──
const snapshot = ref<CatalogSnapshot | null>(null)
const loadingSnapshot = ref(true)
const refreshing = ref(false)
const refreshMsg = ref('')
const areaOptions = ref<{ label: string; value: string }[]>([])
const domainOptions = ref<{ label: string; value: string }[]>([])

// ── 设备浏览 ──
const browserDomain = ref('')
const browserArea = ref('')
const browserKeyword = ref('')
const browserData = ref<CatalogEntity[]>([])
const browserTotal = ref(0)
const browserMatched = ref(0)
const browserOffset = ref(0)
const browserLimit = ref(50)
const browserTruncated = ref(false)
const loadingBrowser = ref(false)

// ── 实时状态 ──
const stateQuery = ref('')
const stateResult = ref<EntityStateResponse | null>(null)
const loadingState = ref(false)

// ── 解析选择器 ──
const resolveQuery = ref('')
const resolveArea = ref('')
const resolveResult = ref<ResolveResponse | null>(null)
const loadingResolve = ref(false)

// ── 别名 ──
const aliases = ref<AliasesResponse | null>(null)
const loadingAliases = ref(false)
const newAliasName = ref('')
const newAliasEntity = ref('')

// ── 解析漏斗 ──
const metrics = ref<ResolveMetricsResponse | null>(null)
const loadingMetrics = ref(false)

async function loadSnapshot() {
  loadingSnapshot.value = true
  try {
    const res = await facade.catalog()
    snapshot.value = res.data
    areaOptions.value = (res.data.areas ?? []).map((a) => ({ label: a, value: a }))
    domainOptions.value = Object.keys(res.data.by_domain ?? {}).map((d) => ({ label: d, value: d }))
  } catch (e) {
    message.error('目录加载失败：' + String(e))
  } finally {
    loadingSnapshot.value = false
  }
}

async function refreshCatalog() {
  refreshing.value = true
  refreshMsg.value = ''
  try {
    const res = await facade.catalogRefresh(true, '', '')
    if (res.data.ok) {
      refreshMsg.value = `已刷新：新增 ${res.data.added}，变更 ${res.data.changed}，移除 ${res.data.removed}，共 ${res.data.total} 个实体`
      message.success('目录已刷新')
      await loadSnapshot()
      await loadBrowser(true)
    } else {
      refreshMsg.value = res.data.error ?? '刷新失败'
      message.error(res.data.error ?? '刷新失败')
    }
  } catch (e) {
    message.error('刷新失败：' + String(e))
  } finally {
    refreshing.value = false
  }
}

async function loadBrowser(reset = true) {
  if (reset) browserOffset.value = 0
  loadingBrowser.value = true
  try {
    const res = await facade.entities(
      browserDomain.value,
      browserArea.value,
      browserKeyword.value,
      browserLimit.value,
      browserOffset.value,
    )
    browserData.value = (res.data.entities ?? []) as CatalogEntity[]
    browserTotal.value = res.data.total
    browserMatched.value = res.data.matched_count
    browserTruncated.value = res.data.truncated
  } catch (e) {
    message.error('设备浏览失败：' + String(e))
  } finally {
    loadingBrowser.value = false
  }
}

function nextPage() {
  if (browserTruncated.value) {
    browserOffset.value += browserLimit.value
    loadBrowser(false)
  }
}
function prevPage() {
  if (browserOffset.value > 0) {
    browserOffset.value = Math.max(0, browserOffset.value - browserLimit.value)
    loadBrowser(false)
  }
}

async function loadState() {
  if (!stateQuery.value.trim()) {
    message.warning('请输入 entity_id')
    return
  }
  loadingState.value = true
  stateResult.value = null
  try {
    const res = await facade.entityState(stateQuery.value.trim())
    stateResult.value = res.data
    if (!res.data.ok) message.error(res.data.error ?? '查询失败')
  } catch (e) {
    message.error('查询失败：' + String(e))
  } finally {
    loadingState.value = false
  }
}

async function loadResolve() {
  if (!resolveQuery.value.trim()) {
    message.warning('请输入设备名')
    return
  }
  loadingResolve.value = true
  resolveResult.value = null
  try {
    const res = await facade.entitiesResolve(resolveQuery.value.trim(), resolveArea.value, '', 8)
    resolveResult.value = res.data
  } catch (e) {
    message.error('解析失败：' + String(e))
  } finally {
    loadingResolve.value = false
  }
}

async function depositAlias(entity_id: string) {
  try {
    const name = resolveQuery.value.trim()
    const res = await facade.catalogSetAlias(name, entity_id)
    if (res.data.ok) {
      message.success(`已沉淀别名：${name} → ${entity_id}`)
      await loadAliases()
    } else message.error(res.data.error ?? '别名沉淀失败')
  } catch (e) {
    message.error('别名沉淀失败：' + String(e))
  }
}

async function loadAliases() {
  loadingAliases.value = true
  try {
    const res = await facade.catalogAliases()
    aliases.value = res.data
  } catch (e) {
    message.error('别名加载失败：' + String(e))
  } finally {
    loadingAliases.value = false
  }
}

async function addAlias() {
  if (!newAliasName.value.trim() || !newAliasEntity.value.trim()) {
    message.warning('请填写名称与 entity_id')
    return
  }
  try {
    const res = await facade.catalogSetAlias(newAliasName.value.trim(), newAliasEntity.value.trim())
    if (res.data.ok) {
      message.success('别名已添加')
      newAliasName.value = ''
      newAliasEntity.value = ''
      await loadAliases()
    } else message.error(res.data.error ?? '添加失败')
  } catch (e) {
    message.error('添加失败：' + String(e))
  }
}

async function removeAlias(name: string) {
  try {
    const res = await facade.catalogRemoveAlias(name)
    if (res.data.ok) {
      message.success('已删除')
      await loadAliases()
    } else message.error(res.data.error ?? '删除失败')
  } catch (e) {
    message.error('删除失败：' + String(e))
  }
}

async function loadMetrics() {
  loadingMetrics.value = true
  try {
    const res = await facade.catalogResolveMetrics()
    metrics.value = res.data
  } catch (e) {
    message.error('漏斗加载失败：' + String(e))
  } finally {
    loadingMetrics.value = false
  }
}

onMounted(() => {
  loadSnapshot()
  loadAliases()
  loadMetrics()
})

const domainEntries = computed(() => Object.entries(snapshot.value?.by_domain ?? {}))
const aliasList = computed(() => Object.entries(aliases.value?.aliases ?? {}))

const browserColumns: DataTableColumns<CatalogEntity> = [
  {
    title: 'entity_id',
    key: 'entity_id',
    render: (row) => h('code', { style: 'font-size:12px' }, row.entity_id),
  },
  {
    title: '名称',
    key: 'friendly_name',
    render: (row) => h('span', row.friendly_name || '—'),
  },
  {
    title: '域',
    key: 'domain',
    width: 96,
    render: (row) => h(NTag, { size: 'small', bordered: false }, { default: () => row.domain }),
  },
  {
    title: '区域',
    key: 'area',
    width: 110,
    render: (row) => h('span', { style: 'color:#8b93a7' }, row.area || '—'),
  },
  {
    title: '状态',
    key: 'state',
    width: 90,
    render: (row) => h('span', row.state ?? '—'),
  },
  {
    title: '连通',
    key: 'connectivity_tier',
    width: 90,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.offline_now ? 'error' : 'default' },
        { default: () => (row.offline_now ? '离线' : row.connectivity_tier) },
      ),
  },
]

const METRIC_BUCKETS = [
  { key: 'exact', label: '精确命中', color: '#10b981' },
  { key: 'medium', label: '模糊命中', color: '#3b82f6' },
  { key: 'low', label: '弱匹配', color: '#f59e0b' },
  { key: 'ambiguous', label: '歧义', color: '#a855f7' },
  { key: 'none', label: '无候选', color: '#ef4444' },
] as const
</script>

<template>
  <div>
    <div class="page-head">
      <h1>设备目录</h1>
      <p class="sub">v1.1.0 实体事实内建 · 自然语言设备名 → 真实 entity_id</p>
    </div>

    <n-alert v-if="refreshMsg" type="info" style="margin-bottom: 16px">{{ refreshMsg }}</n-alert>

    <n-tabs type="line" animated>
      <!-- 概览 -->
      <n-tab-pane name="overview" tab="概览">
        <n-card :bordered="false" class="block">
          <n-space justify="space-between" align="center">
            <n-statistic label="实体总数" :value="snapshot?.total_entities ?? 0" />
            <n-statistic label="区域数" :value="snapshot?.areas.length ?? 0" />
            <n-statistic label="域种类" :value="domainEntries.length" />
            <div>
              <n-text depth="3" style="font-size: 13px">新鲜度</n-text>
              <div style="font-size: 13px">{{ snapshot?.freshness || '—' }}</div>
            </div>
            <n-button type="primary" :loading="refreshing" @click="refreshCatalog">
              刷新目录（拉 HA 全屋）
            </n-button>
          </n-space>
        </n-card>

        <n-card title="按域分布" :bordered="false" class="block">
          <n-empty v-if="!domainEntries.length" description="目录为空，请先刷新" />
          <n-grid v-else :x-gap="12" :y-gap="12" cols="2 s:3 m:4" responsive="screen" item-responsive>
            <n-gi v-for="[dom, count] in domainEntries" :key="dom">
              <n-statistic :label="dom" :value="count" />
            </n-gi>
          </n-grid>
        </n-card>

        <n-card title="区域列表" :bordered="false" class="block">
          <n-space>
            <n-tag v-for="a in snapshot?.areas ?? []" :key="a" round :bordered="false">{{ a }}</n-tag>
            <n-text v-if="!(snapshot?.areas ?? []).length" depth="3">无区域信息</n-text>
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- 设备浏览 -->
      <n-tab-pane name="browse" tab="设备浏览">
        <n-card :bordered="false" class="block">
          <n-space align="center" wrap>
            <n-select
              v-model:value="browserDomain"
              :options="domainOptions"
              placeholder="按域过滤"
              clearable
              style="width: 160px"
            />
            <n-select
              v-model:value="browserArea"
              :options="areaOptions"
              placeholder="按区域过滤"
              clearable
              style="width: 160px"
            />
            <n-input
              v-model:value="browserKeyword"
              placeholder="关键字（名称/ID/区域）"
              clearable
              style="width: 220px"
              @keyup.enter="() => loadBrowser()"
            />
            <n-button type="primary" :loading="loadingBrowser" @click="() => loadBrowser(true)">查询</n-button>
            <n-text depth="3" style="font-size: 13px">
              命中 {{ browserMatched }} / 全屋 {{ browserTotal }}
            </n-text>
          </n-space>
        </n-card>

        <n-card :bordered="false" class="block">
          <n-data-table
            :columns="browserColumns"
            :data="browserData"
            :loading="loadingBrowser"
            :bordered="false"
            :row-key="(row: CatalogEntity) => row.entity_id"
          />
          <n-space justify="space-between" style="margin-top: 12px">
            <n-button :disabled="browserOffset === 0" @click="prevPage">上一页</n-button>
            <n-text depth="3" style="font-size: 13px">
              偏移 {{ browserOffset }} · {{ browserTruncated ? '还有更多' : '已到末尾' }}
            </n-text>
            <n-button :disabled="!browserTruncated" type="primary" @click="nextPage">下一页</n-button>
          </n-space>
        </n-card>
      </n-tab-pane>

      <!-- 实时状态 -->
      <n-tab-pane name="state" tab="实时状态">
        <n-card :bordered="false" class="block">
          <n-space align="center">
            <n-input
              v-model:value="stateQuery"
              placeholder="entity_id，如 light.study_main"
              clearable
              style="width: 320px"
              @keyup.enter="loadState"
            />
            <n-button type="primary" :loading="loadingState" @click="loadState">查询状态</n-button>
          </n-space>
        </n-card>

        <n-card v-if="stateResult" :bordered="false" class="block">
          <n-alert v-if="!stateResult.ok" type="error" :title="stateResult.error">{{ stateResult.hint }}</n-alert>
          <template v-else>
            <n-space align="center">
              <n-statistic label="entity_id" :value="stateResult.entity_id" />
              <n-tag :type="stateResult.source === 'live' ? 'success' : 'warning'" round :bordered="false">
                {{ stateResult.source === 'live' ? '实时 HA' : '目录缓存' }}
              </n-tag>
              <n-statistic label="当前状态" :value="stateResult.state ?? '—'" />
            </n-space>
            <div class="kv" v-if="stateResult.friendly_name">名称：{{ stateResult.friendly_name }}</div>
            <div class="kv">域：{{ stateResult.domain }}</div>
            <div class="kv" v-if="stateResult.note">说明：{{ stateResult.note }}</div>
            <div class="kv" v-if="stateResult.freshness">缓存时间：{{ stateResult.freshness }}</div>
            <div class="kv">可能状态：<n-tag v-for="s in stateResult.possible_states" :key="s" size="small" :bordered="false" style="margin-right:6px">{{ s }}</n-tag></div>
          </template>
        </n-card>
      </n-tab-pane>

      <!-- 解析选择器 -->
      <n-tab-pane name="resolve" tab="解析选择器">
        <n-card :bordered="false" class="block">
          <n-space align="center" wrap>
            <n-input
              v-model:value="resolveQuery"
              placeholder="自然语言设备名，如「书房吊灯」"
              clearable
              style="width: 240px"
              @keyup.enter="loadResolve"
            />
            <n-select
              v-model:value="resolveArea"
              :options="areaOptions"
              placeholder="区域提示（可选）"
              clearable
              style="width: 160px"
            />
            <n-button type="primary" :loading="loadingResolve" @click="loadResolve">解析</n-button>
          </n-space>
          <n-alert v-if="resolveResult?.note" type="default" style="margin-top: 12px">{{ resolveResult.note }}</n-alert>
        </n-card>

        <n-card v-if="resolveResult" title="候选 entity_id" :bordered="false" class="block">
          <n-empty v-if="!resolveResult.candidates.length" description="无候选（目录未刷新或设备名有误）" />
          <n-list v-else>
            <n-list-item v-for="c in resolveResult.candidates" :key="c.entity_id">
              <n-thing :title="c.entity_id" :description="`${c.domain} · ${c.area || '—'} · 匹配：${c.matched_by}`">
                <template #header-extra>
                  <n-space>
                    <n-tag :type="c.confidence === 'high' ? 'success' : c.confidence === 'medium' ? 'info' : 'warning'" size="small" :bordered="false" round>
                      {{ c.confidence }}
                    </n-tag>
                    <n-tag v-if="c.offline_now" type="error" size="small" :bordered="false">离线</n-tag>
                    <n-button size="small" type="primary" @click="depositAlias(c.entity_id)">沉淀为别名</n-button>
                  </n-space>
                </template>
                <div class="kv">名称：{{ c.friendly_name }}</div>
                <div class="kv">集成：{{ c.integration || '—' }} · 连通档：{{ c.connectivity_tier }}</div>
              </n-thing>
            </n-list-item>
          </n-list>
        </n-card>
      </n-tab-pane>

      <!-- 别名管理 -->
      <n-tab-pane name="aliases" tab="别名">
        <n-card title="新增别名" :bordered="false" class="block">
          <n-space align="center">
            <n-input v-model:value="newAliasName" placeholder="设备名，如「书房吊灯」" clearable style="width: 220px" />
            <n-input v-model:value="newAliasEntity" placeholder="entity_id" clearable style="width: 260px" />
            <n-button type="primary" @click="addAlias">添加</n-button>
          </n-space>
        </n-card>

        <n-card title="已沉淀别名" :bordered="false" class="block">
          <n-empty v-if="!aliasList.length" description="暂无别名" />
          <n-list v-else>
            <n-list-item v-for="[name, eid] in aliasList" :key="name">
              <n-thing :title="name" :description="eid">
                <template #header-extra>
                  <n-button size="small" type="error" quaternary @click="removeAlias(name)">删除</n-button>
                </template>
              </n-thing>
            </n-list-item>
          </n-list>
        </n-card>
      </n-tab-pane>

      <!-- 解析漏斗 -->
      <n-tab-pane name="metrics" tab="解析漏斗">
        <n-card :bordered="false" class="block">
          <n-space justify="space-between" align="center">
            <n-statistic label="解析总次数" :value="metrics?.total ?? 0" />
            <n-statistic
              label="成功率"
              :value="metrics?.success_rate == null ? '—' : (metrics.success_rate * 100).toFixed(1) + '%'"
            />
            <n-text depth="3" style="font-size:13px">更新于 {{ metrics?.updated_at || '—' }}</n-text>
            <n-button :loading="loadingMetrics" @click="loadMetrics">刷新</n-button>
          </n-space>
          <div style="margin-top: 20px">
            <div v-for="b in METRIC_BUCKETS" :key="b.key" class="metric-row">
              <div class="metric-label">{{ b.label }}</div>
              <n-progress
                type="line"
                :percentage="metrics && metrics.total ? Math.round((metrics.buckets[b.key] / metrics.total) * 100) : 0"
                :color="b.color"
                :height="14"
                :show-indicator="false"
              />
              <div class="metric-count">{{ metrics?.buckets[b.key] ?? 0 }}</div>
            </div>
          </div>
        </n-card>
      </n-tab-pane>
    </n-tabs>
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
.kv {
  font-size: 13px;
  color: #6b7280;
  margin-top: 4px;
}
.metric-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.metric-label {
  width: 80px;
  font-size: 13px;
  color: #4b5563;
  flex-shrink: 0;
}
.metric-count {
  width: 48px;
  text-align: right;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: #6b7280;
  flex-shrink: 0;
}
</style>
