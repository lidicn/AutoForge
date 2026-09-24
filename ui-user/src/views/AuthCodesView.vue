<template>
  <div class="auth-codes-view">
    <h2 class="section-title">授权码</h2>
    <p class="page-hint">agent 写好自动化后部署时需要授权码。有授权码则直接部署，没有则进待批。</p>

    <!-- Long-term codes -->
    <div class="section">
      <h3 class="subsection-title">长期授权码</h3>
      <p class="hint">不撤销一直可用，agent 部署自动化时无需再问</p>

      <div v-for="code in longCodes" :key="code.code" class="code-card">
        <div class="code-display">
          <span
            v-for="(digit, i) in code.code.split('')"
            :key="i"
            class="code-digit"
          >{{ digit }}</span>
        </div>
        <div class="code-meta">
          <span>生成于 {{ formatDate(code.created_at) }}</span>
          <n-button size="tiny" type="error" secondary @click="handleDelete(code.code)">
            删除
          </n-button>
        </div>
      </div>

      <n-button type="primary" block secondary style="margin-top: 8px" @click="generateLong">
        生成长期授权码
      </n-button>
    </div>

    <n-divider />

    <!-- Short-term code: only one -->
    <div class="section">
      <h3 class="subsection-title">短期授权码</h3>
      <p class="hint">单次部署授权，到期自动失效</p>

      <!-- Show current active short code -->
      <div v-if="currentShortCode" class="code-card short">
        <div class="code-display">
          <span
            v-for="(digit, i) in currentShortCode.code.split('')"
            :key="i"
            class="code-digit short"
          >{{ digit }}</span>
        </div>
        <div class="code-meta">
          <span class="expiry">{{ countdownText(currentShortCode.expires_at) }}</span>
          <n-button size="tiny" type="error" secondary @click="handleDelete(currentShortCode.code)">
            删除
          </n-button>
        </div>
      </div>

      <!-- Generate form (only show when no active short code) -->
      <template v-if="!currentShortCode">
        <div class="slider-row">
          <span>时长</span>
          <n-slider v-model:value="durationMin" :min="5" :max="30" :step="5" />
          <span class="duration-label">{{ durationMin }} 分钟</span>
        </div>
        <n-button type="primary" block style="margin-top: 12px" @click="generateShort">
          生成短期授权码
        </n-button>
      </template>
      <template v-else>
        <n-button block secondary style="margin-top: 8px" @click="refreshShort">
          重新生成
        </n-button>
      </template>
    </div>

    <!-- Newly generated code popup -->
    <n-modal v-model:show="showNewCode" preset="card" title="新授权码" style="max-width: 340px">
      <p class="new-code-hint">把这个码告诉 agent，用于部署当前自动化：</p>
      <div class="new-code-display">
        <span
          v-for="(digit, i) in newCodeDigits"
          :key="i"
          class="code-digit digit-flip"
          :style="{ animationDelay: `${i * 100}ms` }"
        >{{ digit }}</span>
      </div>
      <n-button type="primary" block style="margin-top: 16px" @click="showNewCode = false">
        完成
      </n-button>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { NButton, NSlider, NDivider, NModal, useMessage, useDialog } from 'naive-ui'
import { useMainStore } from '@/stores/main'
import type { AuthCode } from '@/types/api'

const store = useMainStore()
const message = useMessage()
const dialog = useDialog()

const durationMin = ref(15)
const showNewCode = ref(false)
const newCodeDigits = ref<string[]>([])

// Live clock for countdown
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval>
onMounted(() => {
  store.fetchAuthCodes()
  timer = setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => clearInterval(timer))

const longCodes = computed(() =>
  store.authCodes.filter(c => c.type === 'long')
)

// Only show the latest active short code
const currentShortCode = computed<AuthCode | null>(() => {
  const shorts = store.authCodes.filter(c => c.type === 'short')
  if (shorts.length === 0) return null
  // Return the one with latest created_at that hasn't expired
  const now = Date.now()
  for (const c of shorts) {
    if (!c.expires_at) return c
    if (new Date(c.expires_at).getTime() > now) return c
  }
  return null
})

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('zh-CN')
  } catch {
    return iso
  }
}

function countdownText(expiresAt: string | null) {
  if (!expiresAt) return ''
  const diff = new Date(expiresAt).getTime() - now.value
  if (diff <= 0) return '已失效'
  const totalSec = Math.floor(diff / 1000)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return `${m}:${String(s).padStart(2, '0')} 后失效`
}

async function generateLong() {
  const code = await store.generateAuthCode('long')
  newCodeDigits.value = code.code.split('')
  showNewCode.value = true
}

async function generateShort() {
  const code = await store.generateAuthCode('short', durationMin.value)
  newCodeDigits.value = code.code.split('')
  showNewCode.value = true
}

async function refreshShort() {
  if (currentShortCode.value) {
    await store.deleteAuthCode(currentShortCode.value.code)
  }
  await generateShort()
}

function handleDelete(code: string) {
  dialog.warning({
    title: '撤销授权码',
    content: '撤销后持有此码的 agent 将无法再直接部署自动化。',
    positiveText: '撤销',
    negativeText: '取消',
    onPositiveClick: async () => {
      await store.deleteAuthCode(code)
      message.success('已撤销')
    },
  })
}
</script>

<style scoped>
.auth-codes-view {
  padding: 0;
}
.section-title {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 8px;
}
.page-hint {
  font-size: 13px;
  color: #999;
  margin-bottom: 20px;
}
.subsection-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 4px;
}
.hint {
  font-size: 13px;
  color: #999;
  margin-bottom: 12px;
}
.code-card {
  background: white;
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 10px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.code-display {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}
.code-digit {
  width: 36px;
  height: 44px;
  line-height: 44px;
  text-align: center;
  font-size: 22px;
  font-weight: 700;
  font-family: monospace;
  background: #fef3c7;
  border-radius: 8px;
  color: #92400e;
}
.code-digit.short {
  background: #f3f4f6;
  color: #374151;
}
.code-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: #999;
}
.expiry {
  color: #F59E0B;
}
.slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
  font-size: 14px;
}
.slider-row .n-slider {
  flex: 1;
}
.duration-label {
  min-width: 50px;
  text-align: right;
  font-weight: 600;
  color: #F59E0B;
}
.new-code-hint {
  text-align: center;
  color: #999;
  margin-bottom: 16px;
}
.new-code-display {
  display: flex;
  justify-content: center;
  gap: 8px;
}
</style>
