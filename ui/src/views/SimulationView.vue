<script setup lang="ts">
import { computed, h, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NGi,
  NGrid,
  NInput,
  NTag,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { AuditEntry, Instance, SimResponse } from '@/types/api'

const result = ref<SimResponse | null>(null)
const loading = ref(false)
const error = ref('')

const irJson = ref(
  `{"automations":[{"ir_version":"0.2.1","id":"study_day_light","name":"书房白天人来补光","version":1,"mode":"restart","snapshot":true,"meta":{},"nodes":[{"id":"a1","kind":"on","name":"书房检测到人","trigger":{"type":"state","entity_id":"binary_sensor.study_motion","to":"on"}},{"id":"i1","kind":"if","name":"光照不足且灯是关的","expr":{"op":"and","args":[{"op":"lt","left":{"var":"entity.sensor.study_illum","type":"numeric"},"right":{"const":200}},{"op":"is_off","value":{"var":"entity.light.study_main"}}]}},{"id":"d1","kind":"do","name":"开书房主灯","adapter":"ha","action":"light.turn_on","params":{"entity_id":"light.study_main"}},{"id":"p1","kind":"pass","name":"结束"}],"edges":[{"from":"a1","to":"i1","kind":"then"},{"from":"i1","to":"d1","kind":"then"},{"from":"i1","to":"p1","kind":"no"},{"from":"d1","to":"p1","kind":"then"},{"from":"d1","to":"p1","kind":"on_error"}]}]}`,
)
const seedJson = ref(`{"light.x":"off"}`)
const eventsJson = ref(`[{"entity_id":"binary_sensor.m","state":"on"}]`)

async function runSim() {
  loading.value = true
  error.value = ''
  try {
    const ir = JSON.parse(irJson.value)
    const seed = JSON.parse(seedJson.value)
    const events = JSON.parse(eventsJson.value)
    const res = await facade.sim(ir, seed, events)
    result.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

const STATE_TYPE: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  created: 'default',
  active: 'info',
  suspended: 'warning',
  done: 'success',
  cancelled: 'warning',
  failed: 'error',
  expired: 'default',
}

const instanceColumns: DataTableColumns<Instance> = [
  {
    title: '实例',
    key: 'instance_id',
    width: 110,
    render: (row) => h('code', { style: 'font-size:12px' }, row.instance_id),
  },
  {
    title: '状态',
    key: 'state',
    width: 110,
    render: (row) =>
      h(NTag, { size: 'small', bordered: false, type: STATE_TYPE[row.state] ?? 'default' }, { default: () => row.state }),
  },
  {
    title: '当前节点',
    key: 'current_node',
    width: 110,
    render: (row) => h('code', { style: 'font-size:12px' }, row.current_node),
  },
  {
    title: '轨迹',
    key: 'trace',
    render: (row) =>
      h('span', { style: 'font-family:monospace;font-size:12px;color:#4b5563' }, row.trace.map((t) => t.node).join(' → ')),
  },
]

const auditColumns: DataTableColumns<AuditEntry> = [
  { title: '时间', key: 'at', width: 150, render: (row) => h('span', { style: 'font-size:12px;color:#8b93a7' }, row.at) },
  { title: '类型', key: 'type', width: 150, render: (row) => h('code', { style: 'font-size:12px' }, row.type) },
  { title: '实体', key: 'entity_id', width: 160, render: (row) => h('code', { style: 'font-size:12px' }, row.entity_id || '—') },
  { title: '消息', key: 'message' },
]

interface StateRow {
  entity: string
  state: string
}

const stateColumns: DataTableColumns<StateRow> = [
  { title: '实体', key: 'entity', render: (row) => h('code', { style: 'font-size:12px' }, row.entity) },
  {
    title: '状态',
    key: 'state',
    width: 160,
    render: (row) => h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => row.state }),
  },
]

const stateRows = computed<StateRow[]>(() =>
  Object.entries(result.value?.final_states ?? {}).map(([entity, state]) => ({ entity, state })),
)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>仿真回放</h1>
      <p class="sub">输入 IR / Seed / 事件，回放执行轨迹与最终状态</p>
    </div>

    <n-card title="输入" :bordered="false" class="block">
      <n-grid :x-gap="16" :y-gap="12" cols="1 m:3" responsive="screen" item-responsive>
        <n-gi span="1 m:2">
          <div class="field-label">IR（JSON）</div>
          <n-input v-model:value="irJson" type="textarea" :rows="9" class="code-input" />
        </n-gi>
        <n-gi>
          <div class="field-label">Seed</div>
          <n-input v-model:value="seedJson" type="textarea" :rows="3" class="code-input" />
          <div class="field-label" style="margin-top: 10px">Events</div>
          <n-input v-model:value="eventsJson" type="textarea" :rows="3" class="code-input" />
          <n-button type="primary" block :loading="loading" style="margin-top: 12px" @click="runSim">
            ▶ 运行仿真
          </n-button>
        </n-gi>
      </n-grid>
    </n-card>

    <n-alert v-if="error" type="error" title="运行失败" class="block">{{ error }}</n-alert>

    <template v-if="result">
      <n-card title="自然语言结果" :bordered="false" class="block">
        <p class="nl">{{ result.nl }}</p>
      </n-card>

      <n-card title="实例轨迹" :bordered="false" class="block">
        <n-data-table :columns="instanceColumns" :data="result.instances" :bordered="false" />
      </n-card>

      <n-card v-if="result.audit.length" title="审计记录" :bordered="false" class="block">
        <n-data-table :columns="auditColumns" :data="result.audit" :bordered="false" />
      </n-card>

      <n-card v-if="stateRows.length" title="最终状态" :bordered="false" class="block">
        <n-data-table :columns="stateColumns" :data="stateRows" :bordered="false" />
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
.field-label {
  font-size: 13px;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 6px;
}
.code-input :deep(textarea) {
  font-family: monospace;
  font-size: 12.5px;
}
.nl {
  margin: 0;
  background: #f0f7ff;
  border-left: 4px solid #4f46e5;
  padding: 14px 18px;
  border-radius: 0 8px 8px 0;
  line-height: 1.8;
}
</style>
