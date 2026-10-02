<script setup lang="ts">
import { ref, watch } from 'vue'
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
  NSelect,
  NSpace,
  NSpin,
  NTag,
  NThing,
  useMessage,
} from 'naive-ui'
import { facade } from '@/api'
import type { BindResult, ExpectItem, SpecCompileResponse } from '@/types/api'
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

// ── v1.6.0 绑定 ──
const bindResult = ref<BindResult | null>(null)
const binding = ref(false)

/** 编译：AF-Spec 文本 → IR + 诊断。后端把 Spec 语法/校验失败也当**正常响应**返回
 * （HTTP 200 + `ok:false` + `error`），所以这里必须自己按 `ok` 分支，不能只 catch 异常。 */
async function compile() {
  loading.value = true
  error.value = ''
  bindResult.value = null
  try {
    const { data } = await facade.specCompile(specText.value)
    if (data.error) {
      // 语法/校验失败：响应里只有 error，没有可展示的 IR
      error.value = `${data.error.code}：${data.error.message}`
      result.value = null
    } else {
      // ok:false 且无 error = 静态扫描未通过，诊断仍然要展示
      result.value = data
    }
  } catch (e) {
    result.value = null
    error.value = String(e)
    message.error('编译请求失败')
  } finally {
    loading.value = false
  }
}

/** 绑定设备：把 IR 里的 `?占位符` 回填为真实 entity_id（fail-closed，歧义不回填）。 */
async function bindDevices() {
  const ir = result.value?.ir
  if (!ir) {
    message.warning('先编译出 IR 再绑定')
    return
  }
  binding.value = true
  try {
    const { data } = await facade.bind(ir)
    bindResult.value = data
    if (data.ok) message.success(`绑定完成：${data.bound.length}/${data.total}`)
    else message.warning(`绑定未完全：${data.unresolved.length} 项待澄清`)
  } catch (e) {
    bindResult.value = null
    message.error(`绑定请求失败：${e}`)
  } finally {
    binding.value = false
  }
}

// ── v1.7.0 expect 可视化编辑 ──
const expects = ref<ExpectItem[]>([])
const expectDirty = ref(false)
const expectForm = ref({
  type: 'entity' as 'entity' | 'attribute' | 'var',
  entity_id: '',
  state: '',
  attribute: '',
  var: '',
  op: 'eq' as ExpectItem['op'],
  value: '',
  note: '',
})

const opOptions = [
  { label: '等于 (eq)', value: 'eq' },
  { label: '不等于 (ne)', value: 'ne' },
  { label: '小于 (lt)', value: 'lt' },
  { label: '小于等于 (lte)', value: 'lte' },
  { label: '大于 (gt)', value: 'gt' },
  { label: '大于等于 (gte)', value: 'gte' },
]

const expectTypeOptions = [
  { label: '实体状态', value: 'entity' },
  { label: '实体属性', value: 'attribute' },
  { label: '实例变量', value: 'var' },
]

/** 编译后从 IR 同步 expect 到编辑列表 */
function syncExpectsFromIR() {
  if (result.value?.ir?.expect) {
    expects.value = JSON.parse(JSON.stringify(result.value.ir.expect))
  } else {
    expects.value = []
  }
  expectDirty.value = false
}

/** 编译成功后自动同步 */
watch(
  () => result.value?.ir,
  () => syncExpectsFromIR(),
)

/** 生成人读标签 */
function expectLabel(item: ExpectItem): string {
  if (item.var) {
    const op = item.op || 'eq'
    return `变量 \`${item.var}\` ${op} ${JSON.stringify(item.value)}`
  }
  if (item.attribute) {
    const op = item.op || 'eq'
    return `${item.entity_id}.${item.attribute} ${op} ${JSON.stringify(item.value)}`
  }
  const state = Array.isArray(item.state) ? item.state.join(' / ') : item.state
  return `${item.entity_id} → ${state}`
}

/** 尝试把字符串解析为数字/布尔，失败则保留字符串 */
function parseValue(v: string): unknown {
  if (v === 'true') return true
  if (v === 'false') return false
  const n = Number(v)
  if (!Number.isNaN(n) && v.trim() !== '') return n
  return v
}

/** 添加一条断言到编辑列表 */
function addExpect() {
  const f = expectForm.value
  let item: ExpectItem
  if (f.type === 'entity') {
    if (!f.entity_id || !f.state) {
      message.warning('实体形态需要 entity_id 和 state')
      return
    }
    item = { entity_id: f.entity_id, state: f.state }
  } else if (f.type === 'attribute') {
    if (!f.entity_id || !f.attribute || f.value === '') {
      message.warning('属性形态需要 entity_id、attribute 和 value')
      return
    }
    item = { entity_id: f.entity_id, attribute: f.attribute, op: f.op, value: parseValue(f.value) }
  } else {
    if (!f.var || f.value === '') {
      message.warning('变量形态需要 var 和 value')
      return
    }
    item = { var: f.var, op: f.op, value: parseValue(f.value) }
  }
  if (f.note) item.note = f.note
  expects.value.push(item)
  expectDirty.value = true
  expectForm.value = { type: 'entity', entity_id: '', state: '', attribute: '', var: '', op: 'eq', value: '', note: '' }
  message.success('已添加断言，点击「应用到编辑器」生效')
}

/** 删除一条断言 */
function removeExpect(index: number) {
  expects.value.splice(index, 1)
  expectDirty.value = true
}

/** 把编辑后的 expect 写回 specText（替换所有 expect 行） */
function applyExpects() {
  const lines = specText.value.split('\n')
  let insertAt = -1
  const kept: string[] = []
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim()
    if (trimmed.startsWith('expect ')) {
      continue
    }
    if (insertAt === -1 && /^(on|if|do|ask|wait|set|pass)\s/.test(trimmed)) {
      insertAt = kept.length
    }
    kept.push(lines[i])
  }
  const expectLines = expects.value.map((item) => `expect ${JSON.stringify(item)}`)
  if (insertAt === -1) {
    kept.push('', ...expectLines)
  } else {
    kept.splice(insertAt, 0, ...expectLines, '')
  }
  specText.value = kept.join('\n')
  expectDirty.value = false
  message.success('expect 已写入编辑器，请重新编译验证')
}
</script>

<template>
  <div>
    <div class="page-head">
      <h1>AF-Spec 工作台</h1>
      <p class="sub">AF-Spec 文本 → IR 编译 + 诊断扫描 + expect 可视化编辑</p>
    </div>

    <n-grid :x-gap="16" :y-gap="16" cols="1 m:2" responsive="screen" item-responsive>
      <n-gi>
        <n-card title="AF-Spec 编辑" :bordered="false" class="block">
          <n-input v-model:value="specText" type="textarea" :rows="20" class="editor" />
          <n-space style="margin-top: 12px">
            <n-button type="primary" :loading="loading" @click="compile">▶ 编译</n-button>
            <n-button :loading="binding" :disabled="!result?.ir" @click="bindDevices">🔗 绑定设备</n-button>
          </n-space>
        </n-card>

        <!-- expect 可视化编辑面板 -->
        <n-card title="expect 断言管理" :bordered="false" class="block" style="margin-top: 16px">
          <template #header-extra>
            <n-tag :type="expectDirty ? 'warning' : 'success'" size="small">
              {{ expectDirty ? '有未应用修改' : '已同步' }}
            </n-tag>
          </template>

          <p class="expect-hint">
            声明「跑完之后应该是什么」。编译后自动从 IR 同步；编辑后点「应用到编辑器」写回 Spec。
          </p>

          <n-list v-if="expects.length" class="expect-list">
            <n-list-item v-for="(item, idx) in expects" :key="idx">
              <n-thing :title="`#${idx + 1}  ${expectLabel(item)}`">
                <template #header-extra>
                  <n-button text type="error" size="tiny" @click="removeExpect(idx)">删除</n-button>
                </template>
              </n-thing>
            </n-list-item>
          </n-list>
          <n-alert v-else type="info" :bordered="false" style="margin-bottom: 12px">
            暂无断言。编译后会自动同步 IR 中的 expect，或在下方添加。
          </n-alert>

          <div class="expect-form">
            <n-select
              v-model:value="expectForm.type"
              :options="expectTypeOptions"
              style="width: 130px"
              size="small"
            />
            <template v-if="expectForm.type === 'entity'">
              <n-input v-model:value="expectForm.entity_id" placeholder="entity_id（如 light.study_main）" size="small" style="flex: 1" />
              <n-input v-model:value="expectForm.state" placeholder="期望状态（如 on）" size="small" style="width: 140px" />
            </template>
            <template v-else-if="expectForm.type === 'attribute'">
              <n-input v-model:value="expectForm.entity_id" placeholder="entity_id" size="small" style="flex: 1" />
              <n-input v-model:value="expectForm.attribute" placeholder="属性（如 temperature）" size="small" style="width: 130px" />
              <n-select v-model:value="expectForm.op" :options="opOptions" size="small" style="width: 110px" />
              <n-input v-model:value="expectForm.value" placeholder="值" size="small" style="width: 90px" />
            </template>
            <template v-else>
              <n-input v-model:value="expectForm.var" placeholder="变量名（如 vars.x）" size="small" style="flex: 1" />
              <n-select v-model:value="expectForm.op" :options="opOptions" size="small" style="width: 110px" />
              <n-input v-model:value="expectForm.value" placeholder="值" size="small" style="width: 90px" />
            </template>
            <n-button size="small" @click="addExpect">+ 添加</n-button>
          </div>

          <n-space style="margin-top: 12px">
            <n-button type="primary" size="small" :disabled="!expectDirty" @click="applyExpects">
              ✓ 应用到编辑器
            </n-button>
            <n-button size="small" @click="syncExpectsFromIR">↺ 从 IR 重新同步</n-button>
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

            <template v-if="result">
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

            <div v-if="!result && !bindResult" class="hint">
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
.expect-hint {
  margin: 0 0 12px;
  font-size: 12.5px;
  color: #8b93a7;
  line-height: 1.6;
}
.expect-list {
  margin-bottom: 12px;
}
.expect-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 10px;
  background: #f8fafc;
  border-radius: 8px;
}
</style>