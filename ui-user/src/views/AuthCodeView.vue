<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { KeyRound, Plus, Trash2, Clock } from 'lucide-vue-next'
import { useAuthCodesStore } from '../stores/authCodes'

const store = useAuthCodesStore()
const shortTtl = ref(10)
const justCreated = ref<{ code: string; kind: string; expires_at: number | null } | null>(null)
const left = ref(0)
let timer: number | undefined

async function gen(kind: 'long' | 'short') {
  const r = await store.create(kind, kind === 'short' ? shortTtl.value : undefined)
  justCreated.value = { code: r.code, kind, expires_at: r.expires_at ?? null }
  if (r.expires_at) {
    left.value = Math.max(0, Math.round(r.expires_at - Date.now() / 1000))
    if (timer) clearInterval(timer)
    timer = window.setInterval(() => {
      left.value -= 1
      if (left.value <= 0 && timer) clearInterval(timer)
    }, 1000)
  }
}
function fmtLeft() {
  const s = Math.max(0, left.value)
  const m = Math.floor(s / 60)
  return `${m}:${(s % 60).toString().padStart(2, '0')}`
}
const longCodes = computed(() => store.codes.filter((c) => c.kind === 'long'))
const shortCodes = computed(() => store.codes.filter((c) => c.kind === 'short'))

onMounted(() => store.load())
</script>

<template>
  <div class="px-4 py-4 max-w-2xl mx-auto">
    <div class="flex items-center gap-2 mb-3">
      <KeyRound :size="20" class="text-forge-600" />
      <h2 class="text-lg font-semibold text-ink">授权码</h2>
    </div>

    <p class="text-sm text-ink-soft mb-4 leading-relaxed">
      Agent 部署自动化时持有效授权码可<span class="text-forge-700 font-medium">直接生效</span>（路径 A）；否则进入待审批队列由你确认（路径 B）。
    </p>

    <!-- 生成 -->
    <div class="grid grid-cols-2 gap-3 mb-5">
      <button
        class="rounded-2xl bg-forge-500 text-white p-4 text-left active:scale-95 shadow-card"
        @click="gen('long')"
      >
        <Plus :size="18" />
        <div class="font-semibold mt-1">生成长效码</div>
        <div class="text-[11px] opacity-90">可撤销 · 长期有效</div>
      </button>
      <div class="rounded-2xl bg-white shadow-card p-4">
        <div class="flex items-center gap-1 text-ink-soft text-sm">
          <Clock :size="14" /> 短期码
        </div>
        <input
          v-model.number="shortTtl"
          type="range"
          min="5"
          max="30"
          class="w-full mt-2 accent-forge-500"
        />
        <div class="flex items-center justify-between">
          <span class="text-[11px] text-ink-faint">{{ shortTtl }} 分钟</span>
          <button class="text-xs px-3 py-1 rounded-lg bg-forge-500 text-white active:scale-95" @click="gen('short')">
            生成
          </button>
        </div>
      </div>
    </div>

    <!-- 刚生成 -->
    <div
      v-if="justCreated"
      class="rounded-2xl bg-forge-50 border border-forge-200 p-4 mb-5 text-center animate-pop-in"
    >
      <div class="text-xs text-forge-700 mb-1">
        新{{ justCreated.kind === 'long' ? '长效' : '短期' }}授权码（口述给 Agent）
      </div>
      <div class="text-3xl font-bold tracking-[0.3em] text-forge-700 tabular-nums animate-digit-in">
        {{ justCreated.code }}
      </div>
      <div v-if="justCreated.expires_at" class="text-[11px] text-ink-faint mt-1">剩余 {{ fmtLeft() }}</div>
    </div>

    <!-- 长效码列表 -->
    <section class="mb-5">
      <h3 class="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-2">长效授权码</h3>
      <ul v-if="longCodes.length" class="space-y-2">
        <li
          v-for="c in longCodes"
          :key="c.code"
          class="flex items-center justify-between rounded-xl bg-white shadow-card p-3"
        >
          <span class="font-mono text-lg tracking-widest text-ink">{{ c.code }}</span>
          <button class="text-ink-faint hover:text-red-500 p-1.5" @click="store.revoke(c.code)">
            <Trash2 :size="16" />
          </button>
        </li>
      </ul>
      <p v-else class="text-sm text-ink-faint">暂无</p>
    </section>

    <!-- 短期码列表 -->
    <section v-if="shortCodes.length">
      <h3 class="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-2">短期授权码</h3>
      <ul class="space-y-2">
        <li
          v-for="c in shortCodes"
          :key="c.code"
          class="flex items-center justify-between rounded-xl bg-white shadow-card p-3"
        >
          <span class="font-mono text-lg tracking-widest text-ink">{{ c.code }}</span>
          <button class="text-ink-faint hover:text-red-500 p-1.5" @click="store.revoke(c.code)">
            <Trash2 :size="16" />
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>
