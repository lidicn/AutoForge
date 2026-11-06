<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NSpace,
  NSpin,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { facade } from '@/api'
import type { AskAnswerHistoryItem, AskItem, AsksResponse } from '@/types/api'
import AskControl from '@/components/AskControl.vue'

const data = ref<AsksResponse | null>(null)
// v1.28 R4：已应答历史（answered[]，新→旧最多 5 条）。缺字段与空数组必须分开看：
// 前者＝这份读数没带历史，后者＝最近确实没有应答；⛔ 不把「没读到」渲染成「没有」。
const answeredItems = ref<AskAnswerHistoryItem[]>([])
const answeredFieldSeen = ref(false)
const loading = ref(true)
const error = ref('')
const answers = reactive<Record<string, unknown>>({})
const submitting = ref<string | null>(null)
const message = useMessage()
let timer: number | undefined

/** 旧 sidecar 项可能不带 control → 退化成纯文本输入 */
function normalize(it: AskItem): AskItem {
  return {
    ...it,
    control: it.control ?? { widget: 'input', kind: 'text', prompt: it.prompt },
  }
}

async function load() {
  try {
    // 双源：进程内会话（仿真可见）+ watch sidecar（真机可见），按 ask_id 去重
    const [main, side] = await Promise.all([facade.asksPending(), facade.asksSidecar()])
    const mainData = ((main as any).data ?? {}) as AsksResponse
    const sideData = ((side as any).data ?? {}) as AsksResponse
    const merged = new Map<string, AskItem>()
    for (const it of sideData.asks ?? []) merged.set(it.ask_id, normalize(it as AskItem))
    for (const it of mainData.asks ?? []) merged.set(it.ask_id, normalize(it as AskItem))
    data.value = { ok: true, total: merged.size, asks: [...merged.values()] }

    // 已应答历史同样双源：任一源带了 answered[] 就算「后端下发了历史」（哪怕空数组），
    // 两源都缺才是「这份读数没带历史」——这两种情况的文案不同，⛔ 不许混。
    const hist = new Map<string, AskAnswerHistoryItem>()
    let seen = false
    for (const src of [sideData, mainData]) {
      if (Array.isArray(src.answered)) {
        seen = true
        for (const h of src.answered as AskAnswerHistoryItem[]) {
          if (h && h.ask_id) hist.set(h.ask_id, h)
        }
      }
    }
    answeredItems.value = [...hist.values()]
      .sort((a, b) => String(b.answered_at ?? '').localeCompare(String(a.answered_at ?? '')))
      .slice(0, 5)
    answeredFieldSeen.value = seen
    error.value = ''
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

function formatAnsweredAt(at?: string | null): string {
  if (!at) return '（后端未给时间）'
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return `（时间读不出：${at}）`
  return d.toLocaleString()
}

function isAnswered(a: AskItem): boolean {
  const v = answers[a.ask_id]
  if (a.control.widget === 'time_range') {
    return Array.isArray(v) && v.length === 2 && v.every((x) => Number.isFinite(Number(x)))
  }
  return v != null && v !== ''
}

async function submit(a: AskItem) {
  submitting.value = a.ask_id
  try {
    const v = answers[a.ask_id]
    const payload: Record<string, unknown> = { ask_id: a.ask_id, room: a.room }
    if (a.control.widget === 'input' && a.control.kind === 'text') {
      payload.text = String(v ?? '')
    } else {
      payload.answer = { kind: a.control.kind, value: v }
    }
    // 有 session_id 走会话端点（同步，校验被拒会返回 422，可改值重答）
    if (a.session_id) {
      await facade.sessionAnswer(a.session_id, payload)
    } else {
      await facade.askAnswer(payload)
    }
    message.success('已提交应答，运行时将自动继续')
    delete answers[a.ask_id]
    await load()
  } catch (e) {
    message.error(`提交失败（会话保持挂起，可修改后重答）：${String(e)}`)
  } finally {
    submitting.value = null
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(load, 4000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page-container">
    <div class="page-head">
      <div>
        <h2 class="page-title">待应答 Ask</h2>
        <p class="page-sub">
          自动化在 <b>ask</b> 节点挂起时，这里列出需要你拍板的原生控件（下拉 / 滑杆 / 时间窗 / 实体 / 文本）。
          控件形态由后端 <code>AskSpec.control()</code> 给出；应答不合规会被拒绝并<b>保持挂起</b>，可改值重答。
        </p>
      </div>
      <n-button tertiary size="small" @click="load">刷新</n-button>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" class="block">
      {{ error }}
    </n-alert>

    <div v-if="loading" class="block">
      <n-space justify="center" align="center" style="padding: 40px 0">
        <n-spin size="medium" />
        <n-text depth="3">正在加载待应答…</n-text>
      </n-space>
    </div>

    <n-empty v-else-if="!data || data.asks.length === 0" description="当前没有待应答的 ask" class="block" />

    <n-space v-else vertical :size="12">
      <n-card v-for="a in data.asks" :key="a.ask_id" :title="a.prompt || '(无提示)'" size="small">
        <template #header-extra>
          <n-space :size="6">
            <n-tag size="small" type="info">{{ a.control.widget }}</n-tag>
            <n-tag v-if="a.room" size="small" :bordered="false">{{ a.room }}</n-tag>
            <n-tag v-if="a.automation_id" size="small" :bordered="false">自动化 {{ a.automation_id }}</n-tag>
            <n-tag v-if="a.answered_via === 'xiaomi'" size="small" type="success">经小爱应答</n-tag>
            <n-tag v-else-if="a.answered_via === 'webui'" size="small" :bordered="false">WebUI 应答</n-tag>
          </n-space>
        </template>

        <AskControl
          :control="a.control"
          :model-value="answers[a.ask_id]"
          @update:model-value="(v: unknown) => (answers[a.ask_id] = v)"
        />

        <template #action>
          <n-space justify="end">
            <n-button
              type="primary"
              size="small"
              :disabled="!isAnswered(a)"
              :loading="submitting === a.ask_id"
              @click="submit(a)"
            >
              提交应答
            </n-button>
          </n-space>
        </template>
      </n-card>
    </n-space>

    <n-card v-if="!loading" title="最近已应答" :bordered="false" style="margin-top: 16px">
      <template #header-extra>
        <n-text depth="3" style="font-size: 12px">最多 5 条 · 新→旧</n-text>
      </template>
      <n-alert v-if="!answeredFieldSeen" type="warning" :bordered="false">
        后端这一轮没下发 <code>answered</code> 历史字段（⛔ 不等于「没有应答过」：只表示这份读数没带历史）
      </n-alert>
      <n-empty v-else-if="answeredItems.length === 0" description="最近没有已应答记录" size="small" />
      <div v-else>
        <div
          v-for="h in answeredItems"
          :key="h.ask_id"
          style="display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0; border-top: 1px solid #eef0f4"
        >
          <div style="min-width: 0">
            <n-text>{{ h.prompt || '(无提示)' }}</n-text>
            <div style="color: #8b93a7; font-size: 12px; margin-top: 2px">
              {{ formatAnsweredAt(h.answered_at) }}
              <span v-if="h.automation_id"> · 自动化 {{ h.automation_id }}</span>
              <span v-if="h.room"> · {{ h.room }}</span>
              <span v-if="h.edge"> · 边 {{ h.edge }}</span>
            </div>
          </div>
          <n-tag v-if="h.answered_via === 'xiaomi'" size="small" type="success">经小爱应答</n-tag>
          <n-tag v-else-if="h.answered_via === 'webui'" size="small" :bordered="false">WebUI 应答</n-tag>
        </div>
      </div>
    </n-card>
  </div>
</template>
