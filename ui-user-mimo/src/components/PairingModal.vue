<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'
import UiIcon from './UiIcon.vue'
import { useMainStore } from '../stores/main.ts'
import { errorMessage, formatCountdown, formatDateTime } from '../logic/format.ts'
import { PAIR_CODE_LENGTH, PAIR_TTL_MS, countdownMs, flipDelays, isExpired } from '../logic/pairing.ts'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean]; paired: [name: string] }>()

const store = useMainStore()
const message = useMessage()
const hint = ref('')
const step = ref<'form' | 'code'>('form')
const busy = ref(false)

const pair = computed(() => store.pair)
const digits = computed(() => (pair.value ? pair.value.code.split('') : []))
const delays = flipDelays(PAIR_CODE_LENGTH)
const remainMs = computed(() => (pair.value ? countdownMs(pair.value.expires_at, store.now) : 0))
const expired = computed(() => (pair.value ? isExpired(pair.value.expires_at, store.now) : true))
const progress = computed(() => Math.max(0, Math.min(1, remainMs.value / PAIR_TTL_MS)))
const hot = computed(() => remainMs.value > 0 && remainMs.value < 60000)

watch(() => props.show, (v) => {
  if (v) { step.value = store.pair ? 'code' : 'form'; busy.value = false }
})
watch(pair, (v) => { if (v) step.value = 'code' })

async function generate () {
  busy.value = true
  try {
    await store.startPair(hint.value)
    step.value = 'code'
  } catch (e) { message.error(errorMessage(e)) } finally { busy.value = false }
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
    :style="{ width: 'min(92vw, 420px)' }"
    content-style="padding-top: 8px"
    @update:show="onUpdateShow"
  >
    <div class="pair">
      <template v-if="step === 'form'">
        <p class="lead">给这台 Agent 起个名字，生成 6 位配对码，然后在 Agent 端填入完成绑定。</p>
        <n-input v-model:value="hint" placeholder="例如：客厅主控" :maxlength="24" show-count clearable />
        <n-button type="primary" block class="go" :loading="busy" @click="generate">生成配对码</n-button>
      </template>

      <template v-else>
        <div class="micro"><i class="tick" /> 配对码 · {{ pair?.agent_name_hint }}</div>

        <div class="halo" :class="{ dead: expired }">
          <div class="cells">
            <span
              v-for="(d, i) in digits"
              :key="`${pair?.code}-${i}`"
              class="cell"
              :style="{ animationDelay: `${delays[i]}ms` }"
            >{{ d }}</span>
          </div>
        </div>

        <div class="countdown">
          <span class="num cd" :class="{ hot }">{{ formatCountdown(remainMs) }}</span>
          <i class="prog" :class="{ hot }"><b :style="{ width: `${progress * 100}%` }" /></i>
        </div>

        <p class="tip" :class="{ bad: expired }">
          {{ expired ? '配对码已过期，请重新生成' : '在 Agent 端填入该配对码后点击下方确认' }}
        </p>
        <p class="sub">生成于 {{ formatDateTime(pair?.created_at ?? pair?.expires_at ?? null) }} · 有效期 5 分钟</p>

        <div class="acts">
          <n-button type="primary" :loading="busy" :disabled="expired" @click="confirmPaired">
            <template #icon><ui-icon name="check" :size="15" /></template>
            我已在 Agent 端填好
          </n-button>
          <n-button secondary :loading="busy" @click="generate">重新生成</n-button>
          <n-button quaternary @click="close">取消</n-button>
        </div>
      </template>
    </div>
  </n-modal>
</template>

<style scoped>
.lead { margin: 0 0 12px; color: var(--ink-2); }
.go { margin-top: 14px; }
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