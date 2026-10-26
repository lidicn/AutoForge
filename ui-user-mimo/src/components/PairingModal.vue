<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'
import UiIcon from './UiIcon.vue'
import { useMainStore } from '../stores/main.ts'
import { errorMessage, formatCountdown, formatDateTime } from '../logic/format.ts'
import { PAIR_TTL_MS, countdownMs, isExpired } from '../logic/pairing.ts'
import type { PairLink } from '../types/api.ts'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; paired: [name: string] }>()

const store = useMainStore()
const message = useMessage()
const hint = ref('')
// form = 输入名 → 生成配对链接；code = SSE 老路径（agent 主动触发）的 8 位码确认
const step = ref<'form' | 'code' | 'link'>('form')
const busy = ref(false)
const link = ref<PairLink | null>(null)

const pair = computed(() => store.pair)
const digits = computed(() => (pair.value ? pair.value.code.split('') : []))
const remainMs = computed(() => {
  const refTime = link.value ? Date.parse(link.value.expires_at) : (pair.value ? Date.parse(pair.value.expires_at) : 0)
  return Math.max(0, refTime - Date.now())
})
const expired = computed(() => {
  const refTime = link.value ? Date.parse(link.value.expires_at) : (pair.value ? Date.parse(pair.value.expires_at) : 0)
  return Date.now() > refTime
})
// 进度条分母：链接径用后端实发 ttl_s（600s，Q6 裁定），老 SSE 码径回退常数 5 分钟。
// 写死分母会让 600s 的链接一打开进度条就从 83% 起跳（5/6），看着像"快过期了"。
const progress = computed(() => {
  const totalMs = link.value?.ttl_s ? link.value.ttl_s * 1000 : PAIR_TTL_MS
  return Math.max(0, Math.min(1, remainMs.value / totalMs))
})
const hot = computed(() => remainMs.value > 0 && remainMs.value < 60000)

watch(() => props.show, (v) => {
  if (v) {
    // SSE 老路径有未决配对 → 直接进码确认步；否则进新链接生成步
    step.value = store.pair ? 'code' : 'form'
    link.value = null
    busy.value = false
  }
})
watch(pair, (v) => { if (v && step.value !== 'link') step.value = 'code' })

async function generateLink () {
  busy.value = true
  try {
    link.value = await store.createPairLink(hint.value)
    step.value = 'link'
  } catch (e) { message.error(errorMessage(e)) } finally { busy.value = false }
}

async function copyLink () {
  if (!link.value) return
  try {
    await navigator.clipboard.writeText(link.value.link)
    message.success('配对链接已复制，发给 Agent 即可')
  } catch {
    // 剪贴板不可用时回退为手选
    message.info('请手动选中链接复制')
  }
}

async function confirmPaired () {
  busy.value = true
  try {
    const agent = await store.finishPair()
    message.success(`已与「${agent.name}」完成配对`)
    emit('paired', agent.name)
    close()
  } catch (e) { message.error(errorMessage(e)) } finally { busy.value = false }
}

function close () {
  store.cancelPair()
  link.value = null
  step.value = 'form'
  emit('update:show', false)
}

function onUpdateShow (v: boolean) { if (!v) close() }
</script>

<template>
  <n-modal
    :show="props.show"
    preset="card"
    title="配对新 Agent"
    :mask-closable="false"
    :style="{ width: 'min(92vw, 460px)' }"
    content-style="padding-top: 8px"
    @update:show="onUpdateShow"
  >
    <div class="pair">
      <template v-if="step === 'form'">
        <p class="lead">给这台 Agent 起个名字，生成一段配对链接，把链接发给 Agent，它自己就能完成绑定。</p>
        <n-input v-model:value="hint" placeholder="例如：客厅主控" :maxlength="24" show-count clearable />
        <n-button type="primary" block class="go" :loading="busy" @click="generateLink">生成配对链接</n-button>
      </template>

      <template v-else-if="step === 'link'">
        <div class="micro"><i class="tick" /> 配对链接 · {{ hint || link?.agent_name_hint || 'Agent' }}</div>

        <div class="linkbox" :class="{ dead: expired }">
          <code class="linkstr">{{ link?.link }}</code>
        </div>

        <div class="countdown">
          <span class="num cd" :class="{ hot }">{{ formatCountdown(remainMs) }}</span>
          <i class="prog" :class="{ hot }"><b :style="{ width: `${progress * 100}%` }" /></i>
        </div>

        <p class="tip" :class="{ bad: expired }">
          {{ expired ? '链接已过期，请重新生成' : '把这段链接发给 Agent，它打开后自动完成配对' }}
        </p>
        <p class="sub">生成于 {{ formatDateTime(link?.expires_at ?? null) }} · 有效期 {{ Math.round((link?.ttl_s ?? 300) / 60) }} 分钟</p>

        <div class="acts">
          <n-button type="primary" :loading="busy" :disabled="expired" @click="copyLink">
            <template #icon><ui-icon name="copy" :size="15" /></template>
            复制配对链接
          </n-button>
          <n-button secondary :loading="busy" @click="generateLink">重新生成</n-button>
          <n-button quaternary @click="close">取消</n-button>
        </div>
      </template>

      <template v-else>
        <div class="micro"><i class="tick" /> 配对码 · {{ pair?.agent_name_hint }}</div>

        <div class="halo" :class="{ dead: expired }">
          <div class="cells">
            <span
              v-for="(d, i) in digits"
              :key="`${pair?.code}-${i}`"
              class="cell"
            >{{ d }}</span>
          </div>
        </div>

        <div class="countdown">
          <span class="num cd" :class="{ hot }">{{ formatCountdown(remainMs) }}</span>
          <i class="prog" :class="{ hot }"><b :style="{ width: `${progress * 100}%` }" /></i>
        </div>

        <p class="tip" :class="{ bad: expired }">
          {{ expired ? '配对码已过期，请重新发起' : '在 Agent 端填入该配对码后点击下方确认' }}
        </p>
        <p class="sub">生成于 {{ formatDateTime(pair?.created_at ?? pair?.expires_at ?? null) }} · 有效期 5 分钟</p>

        <div class="acts">
          <n-button type="primary" :loading="busy" :disabled="expired" @click="confirmPaired">
            <template #icon><ui-icon name="check" :size="15" /></template>
            我已在 Agent 端填好
          </n-button>
          <n-button secondary :loading="busy" @click="close">取消</n-button>
        </div>
      </template>
    </div>
  </n-modal>
</template>

<style scoped>
.lead { margin: 0 0 12px; color: var(--ink-2); }
.go { margin-top: 14px; }
.micro { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-3); margin-bottom: 8px; }
.micro .tick { width: 6px; height: 6px; border-radius: 50%; background: var(--ok, #16a34a); display: inline-block; }
.linkbox { margin: 14px 0 12px; padding: 12px; border: 1px solid var(--line, #e5e7eb); border-radius: 8px; background: var(--bg-2, #f8fafc); }
.linkbox.dead { opacity: 0.5; }
.linkstr { display: block; font-size: 13px; word-break: break-all; color: var(--ink-1); }
.halo { margin: 14px 0 12px; }
.countdown { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.cd { font-size: 18px; font-weight: 600; color: var(--amber-2); letter-spacing: 0.04em; }
.cd.hot { color: var(--bad); }
.countdown .prog { flex: 1; }
.tip { margin: 0; color: var(--ink-2); }
.tip.bad { color: var(--bad); }
.sub { margin: 4px 0 0; font-size: 12px; color: var(--ink-3); }
.acts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.acts > * { flex: 1 1 auto; }
</style>
