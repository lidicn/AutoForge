<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Users, ListChecks, KeyRound, Settings as SettingsIcon, Flame } from 'lucide-vue-next'
import { api } from './api'
import { useAgentsStore } from './stores/agents'
import { useAutomationsStore } from './stores/automations'
import { useAuthCodesStore } from './stores/authCodes'
import SettingsDrawer from './components/SettingsDrawer.vue'
import PairDialog from './components/PairDialog.vue'
import type { PairEvent } from './types/api'

const route = useRoute()
const agentsStore = useAgentsStore()
const automationsStore = useAutomationsStore()
const authCodesStore = useAuthCodesStore()

const showSettings = ref(false)
const pairVisible = ref(false)
const pairData = ref<PairEvent | null>(null)
const pairLeft = ref(0)
let pairTimer: number | undefined
let es: { close: () => void } | null = null

const nav = [
  { name: 'agent', label: 'Agent', icon: Users },
  { name: 'automations', label: '自动化', icon: ListChecks },
  { name: 'auth-codes', label: '授权码', icon: KeyRound },
]

function startPair() {
  es = api.openPairStream(
    (d) => {
      pairData.value = d
      pairVisible.value = true
      pairLeft.value = Math.max(0, Math.round(d.expires_at - Date.now() / 1000))
      if (pairTimer) clearInterval(pairTimer)
      pairTimer = window.setInterval(() => {
        pairLeft.value -= 1
        if (pairLeft.value <= 0) closePair()
      }, 1000)
    },
    () => {
      /* SSE 断开：静默，下次启动重连 */
    },
  )
}
function closePair() {
  pairVisible.value = false
  if (pairTimer) clearInterval(pairTimer)
}

onMounted(() => {
  agentsStore.load()
  automationsStore.load()
  authCodesStore.load()
  startPair()
})
onUnmounted(() => {
  es?.close()
  if (pairTimer) clearInterval(pairTimer)
})
</script>

<template>
  <div class="min-h-full flex flex-col bg-canvas bg-glow">
    <!-- 顶部栏 -->
    <header
      class="sticky top-0 z-20 flex items-center justify-between px-4 h-14 bg-white/85 backdrop-blur border-b border-black/5"
    >
      <div class="flex items-center gap-2">
        <span class="grid place-items-center w-8 h-8 rounded-xl bg-forge-500 text-white">
          <Flame :size="18" />
        </span>
        <div class="leading-tight">
          <div class="font-semibold text-ink">ForgeSight</div>
          <div class="text-[11px] text-ink-faint">AutoForge 用户端</div>
        </div>
      </div>
      <button
        class="grid place-items-center w-10 h-10 rounded-full hover:bg-black/5 active:scale-95 transition"
        @click="showSettings = true"
        aria-label="设置"
      >
        <SettingsIcon :size="20" class="text-ink-soft" />
      </button>
    </header>

    <!-- 内容区 -->
    <main class="flex-1 overflow-y-auto no-scrollbar pb-24 md:pb-0 md:pl-64">
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </main>

    <!-- 桌面左侧栏 -->
    <aside
      class="nav-sidebar fixed left-0 top-0 bottom-0 w-64 flex-col gap-1 p-4 pt-20 bg-white border-r border-black/5 z-10"
    >
      <div
        v-for="item in nav"
        :key="item.name"
        class="flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition cursor-pointer"
        :class="
          route.name === item.name
            ? 'bg-forge-50 text-forge-700'
            : 'text-ink-soft hover:bg-black/5'
        "
        @click="$router.push({ name: item.name })"
      >
        <component :is="item.icon" :size="18" />
        {{ item.label }}
      </div>
    </aside>

    <!-- 移动底部 Tab -->
    <nav
      class="nav-bottom fixed bottom-0 inset-x-0 z-20 h-16 bg-white/95 backdrop-blur border-t border-black/5 grid grid-cols-3"
    >
      <button
        v-for="item in nav"
        :key="item.name"
        class="flex flex-col items-center justify-center gap-1 text-[11px] transition active:scale-95"
        :class="route.name === item.name ? 'text-forge-600' : 'text-ink-faint'"
        @click="$router.push({ name: item.name })"
      >
        <component :is="item.icon" :size="20" />
        {{ item.label }}
      </button>
    </nav>

    <SettingsDrawer v-model:open="showSettings" />
    <PairDialog :open="pairVisible" :data="pairData" :left="pairLeft" @close="closePair" />
  </div>
</template>
