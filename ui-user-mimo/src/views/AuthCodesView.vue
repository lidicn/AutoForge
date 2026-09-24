<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NEmpty, NPopconfirm, NSlider, NTag, useMessage } from 'naive-ui'
import UiIcon from '../components/UiIcon.vue'
import type { AuthCode } from '../types/api.ts'
import { useMainStore } from '../stores/main.ts'
import { errorMessage, formatCountdown, formatDateTime } from '../logic/format.ts'
import {
  SHORT_MAX_MINUTES,
  SHORT_MIN_MINUTES,
  codeKindMeta,
  validateShortMinutes,
} from '../logic/authcodes.ts'

const store = useMainStore()
const message = useMessage()

const minutes = ref(10)
const busy = ref(false)
const copiedKey = ref<string | null>(null)

const short = computed(() => store.activeShort)
const remain = computed(() => store.shortRemain)
const valid = computed(() => validateShortMinutes(minutes.value))
const shortDigits = computed(() => (short.value ? short.value.code.split('') : []))
const hot = computed(() => remain.value > 0 && remain.value < 60000)
const progress = computed(() => {
  const c = short.value
  if (!c || !c.expires_at) return 0
  const total = Date.parse(c.expires_at) - Date.parse(c.created_at)
  if (total <= 0) return 0
  return Math.max(0, Math.min(1, remain.value / total))
})

async function copyCode (code: string, key: string) {
  try {
    await navigator.clipboard.writeText(code)
    copiedKey.value = key
    setTimeout(() => { if (copiedKey.value === key) copiedKey.value = null }, 1500)
    message.success('已复制到剪贴板')
  } catch {
    message.error('复制失败')
  }
}

async function createShort () {
  busy.value = true
  try {
    await store.createShortCode(minutes.value)
    message.success('短期码已生成，倒计时开始')
  } catch (e) { message.error(errorMessage(e)) } finally { busy.value = false }
}

async function createLong () {
  try {
    const c = await store.createLongCode()
    message.success(`已生成长期码 ${c.code}`)
  } catch (e) { message.error(errorMessage(e)) }
}

async function revoke (code: string) {
  try {
    await store.revokeCode(code)
    message.success('授权码已作废')
  } catch (e) { message.error(errorMessage(e)) }
}
</script>

<template>
  <div class="codes">
    <section class="card short-card" :class="{ live: !!short }">
      <div class="micro">
        <i class="tick" /> 短期码
        <span class="since">{{ codeKindMeta('short').hint }}</span>
      </div>

      <template v-if="short">
        <div class="halo">
          <div class="cells">
            <span
              v-for="(d, i) in shortDigits"
              :key="`${short.code}-${i}`"
              class="cell"
              :style="{ animationDelay: `${i * 70}ms` }"
            >{{ d }}</span>
          </div>
        </div>

        <div class="countdown">
          <span class="num cd" :class="{ hot }">{{ formatCountdown(remain) }}</span>
          <i class="prog" :class="{ hot }"><b :style="{ width: `${progress * 100}%` }" /></i>
        </div>

        <p class="meta num">
          生成于 {{ formatDateTime(short.created_at) }} · 过期于 {{ formatDateTime(short.expires_at) }}
        </p>

        <div class="actions">
          <button
            class="copy-btn"
            :class="{ copied: copiedKey === 'short' }"
            @click="copyCode(short.code, 'short')"
          >
            <ui-icon name="copy" :size="12" />
            {{ copiedKey === 'short' ? '已复制' : '复制' }}
          </button>
          <n-button size="small" secondary type="error" @click="revoke(short.code)">
            <template #icon><ui-icon name="close" :size="14" /></template>
            立即作废
          </n-button>
        </div>
      </template>

      <template v-else>
        <p class="hint">短期码限时有效，过期即失效；同一时刻最多存在一个。</p>
        <div class="slider-row">
          <span class="num minutes">{{ minutes }} 分钟</span>
          <n-slider
            v-model:value="minutes"
            :min="SHORT_MIN_MINUTES"
            :max="SHORT_MAX_MINUTES"
            :step="1"
          />
        </div>
        <div class="scale">
          <span>{{ SHORT_MIN_MINUTES }} 分钟</span>
          <span>{{ SHORT_MAX_MINUTES }} 分钟</span>
        </div>
        <n-button type="primary" block :loading="busy" :disabled="!valid.ok" class="go" @click="createShort">
          生成短期码
        </n-button>
      </template>
    </section>

    <header class="row">
      <h2 class="sec-title">长期码 <span class="num count">{{ store.longCodes.length }}</span></h2>
      <n-button size="small" secondary @click="createLong">
        <template #icon><ui-icon name="plus" :size="15" /></template>
        新建长期码
      </n-button>
    </header>

    <ul v-if="store.longCodes.length" class="list">
      <li v-for="c in store.longCodes" :key="c.code" class="card code-row">
        <div class="code-main">
          <code class="code num">{{ c.code }}</code>
          <div class="meta">
            <n-tag size="tiny" :bordered="false" :type="codeKindMeta(c.type).tagType">
              {{ codeKindMeta(c.type).label }}
            </n-tag>
            <span class="num time">{{ formatDateTime(c.created_at) }} 创建</span>
          </div>
        </div>
        <div class="code-actions">
          <button
            class="copy-btn"
            :class="{ copied: copiedKey === c.code }"
            @click="copyCode(c.code, c.code)"
          >
            <ui-icon name="copy" :size="12" />
            {{ copiedKey === c.code ? '已复制' : '复制' }}
          </button>
          <n-popconfirm @positive-click="revoke(c.code)" positive-text="作废" negative-text="取消">
            <template #trigger>
              <button class="icon-btn danger" title="作废"><ui-icon name="trash" :size="16" /></button>
            </template>
            作废后该授权码立即失效，确认？
          </n-popconfirm>
        </div>
      </li>
    </ul>
    <n-empty v-else description="暂无长期码" size="small" />
  </div>
</template>

<style scoped>
.short-card { padding: 16px; }
.since { margin-left: 6px; letter-spacing: 0; text-transform: none; }
.short-card .halo { margin: 14px 0 12px; }
.countdown { display: flex; align-items: center; gap: 10px; }
.cd { font-size: 22px; font-weight: 600; color: var(--amber); letter-spacing: 0.04em; }
.cd.hot { color: var(--bad); }
.countdown .prog { flex: 1; }
.meta { margin: 10px 0 12px; font-size: 11px; color: var(--ink-3); }
.hint { margin: 10px 0 14px; font-size: 13px; color: var(--ink-2); }
.slider-row { display: flex; align-items: center; gap: 14px; }
.minutes { font-size: 15px; font-weight: 600; color: var(--amber); min-width: 68px; }
.slider-row :deep(.n-slider) { flex: 1; }
.scale { display: flex; justify-content: space-between; font-size: 10px; color: var(--ink-3); margin-top: 2px; }
.go { margin-top: 16px; }
.actions { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
.row {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin: 22px 0 10px;
}
.sec-title { margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; color: var(--ink-2); }
.count { color: var(--ink-3); font-size: 12px; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.code-row { display: flex; align-items: center; gap: 10px; padding: 12px 14px; }
.code-main { flex: 1; min-width: 0; }
.code {
  display: block; font-size: 15px; letter-spacing: 0.06em; color: var(--amber);
  overflow-wrap: anywhere;
}
.code-main .meta { display: flex; align-items: center; gap: 8px; margin: 6px 0 0; }
.time { font-size: 11px; color: var(--ink-3); }
.code-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
</style>
