<script setup lang="ts">
import { ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NGi,
  NGrid,
  NInput,
  NList,
  NListItem,
  NSpin,
  NThing,
  useMessage,
} from 'naive-ui'
import { facade } from '@/api'
import type { BindResult, SpecCompileResponse } from '@/types/api'
import DiagnosticPanel from '@/components/DiagnosticPanel.vue'
import SafetyAlert from '@/components/SafetyAlert.vue'

const message = useMessage()

const specText = ref(`automation my_auto
name "我的自动化"
ir_version "0.2.1"
version 1
mode single

on a1 {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"}
if i1 {"op": "lt", "left": {"var": "entity.sensor.study_illum", "type": "numeric"}, "right": {"const": 200}}
do d1 ha.light.turn_on {"entity_id": "light.study_main"}
pass p1

edge a1 -> i1 then
edge i1 -> d1 then
edge i1 -> p1 no
edge d1 -> p1 then
edge d1 -> p1 on_error
`)

const result = ref<SpecCompileResponse | null>(null)
const loading = ref(false)
const error = ref('')

// ── v1.6.0 绑定：把 IR 中的设备描述占位符（?设备名）回填为真实 entity_id ──
const bindResult = ref<BindResult | null>(null)
const binding = ref(false)

async function compile() {
  loading.value = true
  error.value = ''
  bindResult.value = null
  try {
    const res = await facade.specCompile(specText.value)
    result.value = res.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function bind() {
  if (!result.value?.ir) {
    message.warning('请先编译出 IR')
    return
  }
  binding.value = true
  bindResult.value = null
  try {
    const res = await facade.bind(result.value.ir)
    bindResult.value = res.data
    if (res.data.ok) message.success('绑定完成：全部占位符已回填')
    else message.warning(res.data.note)
  } catch (e) {
    message.error('绑定失败：' + String(e))
  } finally {
    binding.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <h1>AF-Spec 工作台</h1>
      <p class="sub">AF-Spec 文本 → IR 编译 + 诊断扫描</p>
    </div>

    <n-grid :x-gap="16" :y-gap="16" cols="1 m:2" responsive="screen" item-responsive>
      <n-gi>
        <n-card title="AF-Spec 编辑" :bordered="false" class="block">
          <n-input v-model:value="specText" type="textarea" :rows="20" class="editor" />
          <n-space style="margin-top: 12px">
            <n-button type="primary" :loading="loading" @click="compile">▶ 编译</n-button>
            <n-button :loading="binding" :disabled="!result?.ir" @click="bind">🔗 绑定设备</n-button>
          </n-space>
        </n-card>
      </n-gi>

      <n-gi>
        <n-card title="编译结果" :bordered="false" class="block">
          <n-spin :show="loading || binding">
            <n-alert v-if="error" type="error" title="编译失败">{{ error }}</n-alert>

            <n-alert
              v-if="bindResult"
              :type="bindResult.ok ? 'success' : 'warning'"
              :title="bindResult.ok ? '绑定完成' : '绑定未完全'"
              style="margin-bottom: 12px"
            >
              {{ bindResult.note }}
            </n-alert>

            <n-list v-if="bindResult?.bound.length" style="margin-bottom: 12px">
              <n-list-item v-for="b in bindResult.bound" :key="b.node + b.from">
                <n-thing :title="`${b.node}.${b.field}`" :description="`${b.from} → ${b.to}（${b.matched_by}）`" />
              </n-list-item>
            </n-list>
            <n-list v-if="bindResult?.unresolved.length" style="margin-bottom: 12px">
              <n-list-item v-for="u in bindResult.unresolved" :key="u.node + u.from">
                <n-thing :title="`${u.node}.${u.field}`" :description="`${u.from} 未绑定：${u.reason}`" />
              </n-list-item>
            </n-list>

            <template v-else-if="result">
              <SafetyAlert
                v-if="
                  result.diagnostics.some(
                    (d) =>
                      d.code === 'L3_ACTION' ||
                      d.code === 'SHADOW_WRITES_DEVICE' ||
                      d.code === 'LOW_CONF_WRITES_DEVICE',
                  )
                "
                :diagnostics="result.diagnostics"
              />

              <n-collapse style="margin-bottom: 12px">
                <n-collapse-item title="IR (JSON)" name="ir">
                  <pre class="code">{{ JSON.stringify(result.ir, null, 2) }}</pre>
                </n-collapse-item>
              </n-collapse>

              <div class="nl-title">自然语言</div>
              <p class="nl">{{ result.nl }}</p>

              <DiagnosticPanel :diagnostics="result.diagnostics" />
            </template>

            <div v-else class="hint">
              <div class="hint-icon">⌘</div>
              点击「编译」查看结果
            </div>
          </n-spin>
        </n-card>
      </n-gi>
    </n-grid>
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
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.editor :deep(textarea) {
  font-family: monospace;
  font-size: 13px;
}
.code {
  margin: 0;
  background: #1e1e2e;
  color: #d4d4d4;
  padding: 14px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.6;
  max-height: 320px;
  overflow-y: auto;
}
.nl-title {
  font-size: 13px;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 6px;
}
.nl {
  margin: 0 0 12px;
  background: #f0f7ff;
  border-left: 4px solid #4f46e5;
  padding: 14px 18px;
  border-radius: 0 8px 8px 0;
  line-height: 1.8;
}
.hint {
  text-align: center;
  color: #a3aab8;
  padding: 60px 20px;
}
.hint-icon {
  font-size: 30px;
  margin-bottom: 10px;
  opacity: 0.4;
}
</style>
