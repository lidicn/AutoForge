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
import type { AskItem, AsksResponse } from '@/types/api'
import AskControl from '@/components/AskControl.vue'

const data = ref<AsksResponse | null>(null)
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
    const merged = new Map<string, AskItem>()
    for (const it of (side as any).data?.asks ?? []) merged.set(it.ask_id, normalize(it as AskItem))
    for (const it of (main as any).data?.asks ?? []) merged.set(it.ask_id, normalize(it as AskItem))
    data.value = { ok: true, total: merged.size, asks: [...merged.values()] }
    error.value = ''
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
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
  </div>
</template>
