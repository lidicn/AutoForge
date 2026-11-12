<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NEmpty,
  NInput,
  NInputNumber,
  NModal,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
import { api } from '../api/index.ts'
import UiIcon from '../components/UiIcon.vue'
import { errorMessage } from '../logic/format.ts'
import { grantScopeText, grantState } from '../logic/grants.ts'
import { useMainStore } from '../stores/main.ts'
import type { Grant, SceneCondition, SceneListResponse, SceneSummary } from '../types/api.ts'

const store = useMainStore()
const message = useMessage()

const scenes = ref<SceneListResponse | null>(null)
const scenesError = ref('')
const scenesBusy = ref(false)
const scenesRefreshedAt = ref('')
const deleting = ref<string | null>(null)

// 可用执行授权：页面自己拉一次（store 未缓存 grants），取不到就退化成手填 grant_id。
const grants = ref<Grant[]>([])
const grantsLoading = ref(false)
const grantsLoaded = ref(false)

// ---------------- 运行 ----------------
const runTarget = ref<SceneSummary | null>(null)
const runGrantId = ref('')
const runAgent = ref('')
const runPhrase = ref('')
const runBusy = ref(false)

const activeGrants = computed(() => grants.value.filter((g) => grantState(g, store.now) === 'active'))
const grantOptions = computed(() =>
  activeGrants.value.map((g) => ({
    label: `${g.grant_id} · ${g.agent || '—'} · ${grantScopeText(g)}`,
    value: g.grant_id,
  })),
)
const runShow = computed({
  get: () => runTarget.value !== null,
  set: (v: boolean) => { if (!v) closeRun() },
})

// ---------------- 新建 ----------------
interface StepDraft {
  adapter: string
  action: string
  target: string
  params: string
  on_error: string
  delay_ms_after: number
}
function blankStep (): StepDraft {
  return { adapter: 'ha', action: '', target: '', params: '', on_error: 'abort', delay_ms_after: 0 }
}

const createShow = ref(false)
const formSceneId = ref('')
const formName = ref('')
const formAliases = ref('')
const formCondition = ref('')
const formSteps = ref<StepDraft[]>([blankStep()])
const saving = ref(false)

const stepOptions = [
  { label: '中止（abort）', value: 'abort' },
  { label: '继续（continue）', value: 'continue' },
]

async function loadScenes () {
  scenesBusy.value = true
  try {
    scenes.value = await api.listScenes()
    scenesRefreshedAt.value = new Date().toLocaleTimeString()
    scenesError.value = ''
  } catch (e) {
    // 读失败保留上次成功读数（若有），⛔ 把失败清成「没有场景」
    scenesError.value = String(errorMessage(e, '场景列表读取失败'))
  } finally {
    scenesBusy.value = false
  }
}

async function loadGrantsIfNeeded () {
  if (grantsLoaded.value || grantsLoading.value) return
  grantsLoading.value = true
  try {
    const resp = await api.listGrants()
    grants.value = Array.isArray(resp?.items) ? resp.items : []
    grantsLoaded.value = true
  } catch {
    // 取不到授权不阻塞运行弹窗：退化成手填 grant_id（回执里写明）
    grants.value = []
  } finally {
    grantsLoading.value = false
  }
}

function conditionText (c: SceneCondition | null): string {
  if (!c) return '无条件'
  return JSON.stringify(c)
}

function openRun (s: SceneSummary) {
  runTarget.value = s
  runGrantId.value = activeGrants.value[0]?.grant_id ?? ''
  runAgent.value = activeGrants.value[0]?.agent ?? ''
  runPhrase.value = s.name
  void loadGrantsIfNeeded().then(() => {
    if (!runGrantId.value && activeGrants.value.length) {
      runGrantId.value = activeGrants.value[0].grant_id
      runAgent.value = activeGrants.value[0].agent ?? ''
    }
  })
}

function closeRun () {
  runTarget.value = null
  runGrantId.value = ''
  runAgent.value = ''
  runPhrase.value = ''
}

function onPickGrant (id: string) {
  runGrantId.value = id
  const g = activeGrants.value.find((x) => x.grant_id === id)
  if (g && !runAgent.value.trim()) runAgent.value = g.agent || ''
}

async function submitRun () {
  const target = runTarget.value
  if (!target) return
  const grantId = runGrantId.value.trim()
  const agent = runAgent.value.trim()
  if (!grantId) { message.error('请选择或填写执行授权（grant_id）'); return }
  if (!agent) { message.error('请填写执行 agent（执行主体）'); return }
  runBusy.value = true
  try {
    const r = await api.runScene(target.scene_id, { phrase: runPhrase.value.trim(), agent, grantId })
    const n = Array.isArray(r?.steps) ? r.steps.length : 0
    message.success(`场景已执行（${n} 步）${r?.code ? ' · ' + r.code : ''}`)
    closeRun()
    await loadScenes()
  } catch (e) {
    message.error(errorMessage(e, '场景执行失败'))
  } finally {
    runBusy.value = false
  }
}

async function removeScene (s: SceneSummary) {
  deleting.value = s.scene_id
  try {
    await api.deleteScene(s.scene_id)
    message.success('已归档进 .scenes/deleted/，可恢复')
    await loadScenes()
  } catch (e) {
    message.error(errorMessage(e, '删除失败'))
  } finally {
    deleting.value = null
  }
}

function openCreate () {
  formSceneId.value = ''
  formName.value = ''
  formAliases.value = ''
  formCondition.value = ''
  formSteps.value = [blankStep()]
  createShow.value = true
}

function addStep () { formSteps.value.push(blankStep()) }
function removeStep (i: number) {
  if (formSteps.value.length <= 1) return
  formSteps.value.splice(i, 1)
}

/** 解析 JSON 对象：空串→null；非对象/坏 JSON→Error（⛔ 静默当空）。 */
function parseJsonObject (text: string): Record<string, unknown> | null | Error {
  const raw = text.trim()
  if (!raw) return null
  try {
    const v = JSON.parse(raw)
    if (v === null) return null
    if (typeof v !== 'object' || Array.isArray(v)) return new Error('必须是 JSON 对象')
    return v as Record<string, unknown>
  } catch {
    return new Error('不是合法 JSON')
  }
}

async function submitCreate () {
  const sid = formSceneId.value.trim()
  const name = formName.value.trim()
  if (!/^[a-z][a-z0-9_]*$/.test(sid)) {
    message.error('scene_id 需以小写字母开头，仅含小写字母、数字、下划线')
    return
  }
  if (!name) { message.error('场景名称不能为空'); return }

  const cond = parseJsonObject(formCondition.value)
  if (cond instanceof Error) {
    message.error(`前置条件格式有误：${cond.message}（留空＝无条件）`)
    return
  }

  const steps: Record<string, unknown>[] = []
  for (let i = 0; i < formSteps.value.length; i++) {
    const s = formSteps.value[i]
    const action = s.action.trim()
    const target = s.target.trim()
    if (!action) { message.error(`第 ${i + 1} 步缺少 action（如 light.turn_on）`); return }
    if (!target) { message.error(`第 ${i + 1} 步缺少 target（设备实体 id）`); return }
    const p = parseJsonObject(s.params)
    if (p instanceof Error) { message.error(`第 ${i + 1} 步 params 格式有误：${p.message}`); return }
    steps.push({
      step_id: `step_${i + 1}`,     // 后端构造期要求 step_id 非空且唯一
      adapter: s.adapter.trim() || 'ha',
      action,
      target,
      params: p ?? {},
      on_error: s.on_error || 'abort',
      delay_ms_after: Number.isFinite(Number(s.delay_ms_after)) ? Math.max(0, Math.trunc(Number(s.delay_ms_after))) : 0,
    })
  }
  if (!steps.length) { message.error('场景至少要有一个步骤'); return }

  const aliases = formAliases.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean)
  saving.value = true
  try {
    await api.saveScene({
      scene_id: sid,
      name,
      aliases,
      condition: cond ?? null,
      steps,
    }, false)
    message.success('场景已创建')
    createShow.value = false
    await loadScenes()
  } catch (e) {
    message.error(errorMessage(e, '创建场景失败'))
  } finally {
    saving.value = false
  }
}

onMounted(() => { void loadScenes() })
</script>

<template>
  <div class="scenes">
    <header class="page-head">
      <h2 class="sec-title">场景 <span class="num count">{{ scenes?.scenes?.length ?? '…' }}</span></h2>
      <div class="head-acts">
        <n-button size="small" secondary :loading="scenesBusy" @click="loadScenes">刷新</n-button>
        <n-button size="small" type="primary" @click="openCreate">
          <template #icon><ui-icon name="plus" :size="15" /></template>
          新建
        </n-button>
      </div>
    </header>
    <p class="sub">一句话触发的批量设备动作清单：按序执行、跑完即止。运行需选一条执行授权（每步独立过 grant）。</p>

    <n-alert v-if="scenesError" type="error" title="场景列表读取失败" class="alert">
      {{ scenesError }}
      <div v-if="scenesRefreshedAt" class="alert-sub">页面保留上次成功读数（{{ scenesRefreshedAt }}）；这不等于「没有场景」。</div>
    </n-alert>
    <p v-if="scenes?.hidden_count" class="hidden-note">另有 {{ scenes.hidden_count }} 条不在你名下</p>

    <div v-if="scenesBusy && !scenes" class="loading"><n-spin size="small" /> <span>加载中…</span></div>

    <ul v-else-if="scenes?.scenes?.length" class="list">
      <li v-for="s in scenes.scenes" :key="s.scene_id" class="card s-row">
        <div class="s-main">
          <div class="s-top">
            <h3 class="s-name">{{ s.name }}</h3>
            <n-tag size="tiny" :bordered="false" type="info">{{ s.scene_id }}</n-tag>
          </div>
          <div class="meta">别名：{{ s.aliases.length ? s.aliases.join('、') : '无' }}</div>
          <div class="meta">{{ s.step_count }} 步 · 归属 {{ s.owner || '—' }}</div>
          <div class="meta" :class="{ cond: !!s.condition }">
            {{ s.condition ? '条件：' + conditionText(s.condition) : '无条件' }}
          </div>
        </div>
        <div class="s-actions">
          <n-button size="small" secondary @click="openRun(s)">运行</n-button>
          <n-popconfirm
            :positive-text="`删除 ${s.name}`"
            negative-text="不删"
            @positive-click="removeScene(s)"
          >
            <template #trigger>
              <n-button size="small" secondary type="error" :loading="deleting === s.scene_id">删除</n-button>
            </template>
            删除会把定义归档进 .scenes/deleted/（可恢复）。确认删除「{{ s.name }}」？
          </n-popconfirm>
        </div>
      </li>
    </ul>
    <n-empty v-else-if="!scenesError && !scenesBusy && scenes" description="当前没有场景" size="small" />

    <!-- 运行场景 -->
    <n-modal
      v-model:show="runShow"
      preset="card"
      title="运行场景"
      :mask-closable="false"
      :style="{ width: 'min(92vw, 460px)' }"
    >
      <div v-if="runTarget" class="run">
        <p class="run-title">{{ runTarget.name }} <code class="num">{{ runTarget.scene_id }}</code></p>
        <div class="field">
          <label>执行授权（grant_id）</label>
          <n-select
            v-if="grantOptions.length"
            :value="runGrantId"
            :options="grantOptions"
            filterable
            clearable
            placeholder="选择一条生效中的执行授权"
            @update:value="onPickGrant"
          />
          <n-input
            v-else
            v-model:value="runGrantId"
            placeholder="手填 grant_id（未读到可用授权列表）"
          />
          <p v-if="grantsLoading" class="hint">正在读取可用执行授权…</p>
          <p v-else-if="!grantOptions.length && grantsLoaded" class="hint">未读到生效中的执行授权，请手填 grant_id。</p>
        </div>
        <div class="field">
          <label>执行 agent</label>
          <n-input v-model:value="runAgent" placeholder="执行主体（如 agt_living）" />
        </div>
        <div class="field">
          <label>触发词（可选）</label>
          <n-input v-model:value="runPhrase" placeholder="本次用哪句话触发（可留空）" />
        </div>
        <div class="modal-acts">
          <n-button type="primary" :loading="runBusy" @click="submitRun">确认运行</n-button>
          <n-button quaternary @click="closeRun">取消</n-button>
        </div>
      </div>
    </n-modal>

    <!-- 新建场景 -->
    <n-modal
      v-model:show="createShow"
      preset="card"
      title="新建场景"
      :mask-closable="false"
      :style="{ width: 'min(94vw, 560px)' }"
      content-style="max-height: 68vh; overflow-y: auto;"
    >
      <div class="create">
        <div class="field">
          <label>scene_id <span class="req">*</span></label>
          <n-input v-model:value="formSceneId" placeholder="小写字母开头，如 hui_jia_mo_shi" />
        </div>
        <div class="field">
          <label>名称 <span class="req">*</span></label>
          <n-input v-model:value="formName" placeholder="如 回家模式" />
        </div>
        <div class="field">
          <label>别名（逗号分隔，可选）</label>
          <n-input v-model:value="formAliases" placeholder="如 回家, 我回来了" />
        </div>
        <div class="field">
          <label>前置条件（JSON，可选；留空＝无条件）</label>
          <n-input
            v-model:value="formCondition"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            placeholder='如 {"entity_id":"sensor.living_illuminance","below":5}'
          />
        </div>

        <div class="steps-head">
          <span>步骤</span>
          <n-button size="tiny" secondary @click="addStep">
            <template #icon><ui-icon name="plus" :size="13" /></template>
            添加一步
          </n-button>
        </div>

        <div v-for="(st, i) in formSteps" :key="i" class="step-card">
          <div class="step-top">
            <span class="num step-no">第 {{ i + 1 }} 步</span>
            <button
              v-if="formSteps.length > 1"
              class="icon-btn danger"
              title="移除这一步"
              @click="removeStep(i)"
            ><ui-icon name="trash" :size="15" /></button>
          </div>
          <div class="grid2">
            <div class="field">
              <label>adapter</label>
              <n-input v-model:value="st.adapter" placeholder="ha" />
            </div>
            <div class="field">
              <label>action <span class="req">*</span></label>
              <n-input v-model:value="st.action" placeholder="light.turn_on" />
            </div>
            <div class="field">
              <label>target <span class="req">*</span></label>
              <n-input v-model:value="st.target" placeholder="light.living_main" />
            </div>
            <div class="field">
              <label>on_error</label>
              <n-select v-model:value="st.on_error" :options="stepOptions" />
            </div>
          </div>
          <div class="field">
            <label>params（JSON，可选）</label>
            <n-input
              v-model:value="st.params"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 4 }"
              placeholder='如 {"brightness": 153}'
            />
          </div>
          <div class="field">
            <label>delay_ms_after（毫秒，默认 0）</label>
            <n-input-number v-model:value="st.delay_ms_after" :min="0" :step="100" style="width: 100%" />
          </div>
        </div>

        <div class="modal-acts">
          <n-button type="primary" :loading="saving" @click="submitCreate">创建场景</n-button>
          <n-button quaternary @click="createShow = false">取消</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.page-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin: 4px 0 6px;
}
.head-acts { display: flex; align-items: center; gap: 8px; }
.sec-title { margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; color: var(--ink-2); }
.count { color: var(--ink-3); font-size: 12px; }
.sub { margin: 0 0 12px; font-size: 12px; color: var(--ink-3); }
.alert { margin-bottom: 10px; }
.alert-sub { margin-top: 4px; font-size: 12px; color: var(--ink-3); }
.hidden-note { margin: 0 0 10px; font-size: 12px; color: var(--amber); }
.loading { display: flex; align-items: center; gap: 8px; padding: 18px 0; color: var(--ink-3); font-size: 13px; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.s-row { display: flex; align-items: center; gap: 10px; padding: 12px 14px; }
.s-main { flex: 1; min-width: 0; }
.s-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.s-name { margin: 0; font-size: 15px; font-weight: 600; }
.meta { margin-top: 6px; font-size: 12px; color: var(--ink-3); overflow-wrap: anywhere; }
.meta.cond { color: var(--amber); }
.s-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }

.run-title { margin: 0 0 12px; font-size: 14px; font-weight: 600; }
.run-title code { color: var(--amber); font-size: 12px; margin-left: 4px; }
.field { margin-bottom: 12px; display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 12px; color: var(--ink-2); }
.req { color: var(--bad); }
.hint { margin: 2px 0 0; font-size: 11px; color: var(--ink-3); }
.modal-acts { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }

.steps-head {
  display: flex; align-items: center; justify-content: space-between;
  margin: 14px 0 8px; font-size: 12px; color: var(--ink-2);
}
.step-card {
  border: 1px solid var(--line-soft); border-radius: 10px; background: var(--bg-sunken);
  padding: 10px 12px; margin-bottom: 10px;
}
.step-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.step-no { font-size: 11px; color: var(--amber); letter-spacing: 0.04em; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 380px) {
  .grid2 { grid-template-columns: 1fr; }
}
</style>
