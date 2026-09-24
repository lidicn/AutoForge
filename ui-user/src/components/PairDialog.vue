<script setup lang="ts">
import { computed } from 'vue'
import { X, ShieldCheck } from 'lucide-vue-next'
import type { PairEvent } from '../types/api'

const props = defineProps<{ open: boolean; data: PairEvent | null; left: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const digits = computed(() => (props.data ? props.data.code.split('') : []))
const hint = computed(() => props.data?.agent_name_hint || '未知 Agent')
const mmss = computed(() => {
  const s = Math.max(0, props.left)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${r.toString().padStart(2, '0')}`
})
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-40 grid place-items-center bg-black/40 px-6 animate-pop-in"
    @click.self="emit('close')"
  >
    <div class="w-full max-w-sm bg-white rounded-2xl shadow-card p-6 animate-pop-in">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2 text-forge-700">
          <ShieldCheck :size="20" />
          <span class="font-semibold">新的配对请求</span>
        </div>
        <button class="text-ink-faint hover:text-ink" @click="emit('close')">
          <X :size="20" />
        </button>
      </div>

      <p class="text-sm text-ink-soft mb-1">
        <span class="font-medium text-ink">{{ hint }}</span> 请求接入，请在
        <span class="text-forge-600 font-medium">AutoForge</span> 中输入下方 6 位码完成配对
      </p>
      <p class="text-[11px] text-ink-faint mb-4">码仅显示在此设备，口述给 Agent 即可。</p>

      <div class="flex justify-center gap-2 mb-4">
        <span
          v-for="(d, i) in digits"
          :key="i"
          class="w-11 h-14 grid place-items-center rounded-xl bg-forge-50 text-forge-700 text-2xl font-bold tabular-nums animate-digit-in"
          :style="{ animationDelay: i * 50 + 'ms' }"
          >{{ d }}</span
        >
      </div>

      <div class="flex items-center justify-between text-xs text-ink-faint">
        <span>有效期</span>
        <span class="tabular-nums text-ink-soft">{{ mmss }}</span>
      </div>
      <div class="mt-3 h-1.5 rounded-full bg-black/5 overflow-hidden">
        <div
          class="h-full bg-forge-500 transition-all"
          :style="{ width: Math.max(0, Math.min(100, (left / 300) * 100)) + '%' }"
        />
      </div>
    </div>
  </div>
</template>
