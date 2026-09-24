<script setup lang="ts">
import { onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, useMessage } from 'naive-ui'
import { useIntervalFn, useOnline } from '@vueuse/core'
import UiIcon from '../components/UiIcon.vue'
import { useMainStore } from '../stores/main.ts'

const store = useMainStore()
const route = useRoute()
const router = useRouter()
const message = useMessage()
const online = useOnline()

const tabs = [
  { name: 'agents', label: 'Agent', icon: 'agent' },
  { name: 'automations', label: '自动化', icon: 'bolt' },
  { name: 'auth-codes', label: '授权码', icon: 'key' },
]

const { pause } = useIntervalFn(() => store.tick(), 1000)

store.tick()
store.refresh()

onUnmounted(pause)

function onToggleTheme () {
  store.toggleTheme()
}

async function onLogout () {
  await store.logout()
  message.success('已退出登录')
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <span class="mark"><ui-icon name="flame" :size="17" /></span>
          <span class="title">AutoForge</span>
        </div>
        <div class="right">
          <span v-if="!online" class="offline">离线</span>
          <span v-else class="user">{{ store.user?.username }}</span>
          <button class="icon-btn" :title="store.darkMode ? '切浅色' : '切深色'" @click="onToggleTheme">
            <ui-icon :name="store.darkMode ? 'sun' : 'moon'" :size="16" />
          </button>
          <button class="icon-btn" title="退出登录" @click="onLogout">
            <ui-icon name="logout" :size="16" />
          </button>
        </div>
      </div>
      <div class="ember-line" />
      <n-alert v-if="store.lastError" class="warn" type="warning" :show-icon="false" :bordered="false">
        {{ store.lastError }}
      </n-alert>
    </header>

    <main class="content">
      <router-view />
    </main>

    <nav class="tabbar">
      <div class="tabbar-inner">
        <div class="tabs">
          <router-link
            v-for="t in tabs"
            :key="t.name"
            class="tab"
            :class="{ active: route.name === t.name }"
            :to="{ name: t.name }"
          >
            <span class="notch" />
            <ui-icon :name="t.icon" :size="21" />
            <span class="label">{{ t.label }}</span>
            <span v-if="t.name === 'automations' && store.pendingCount" class="tab-badge num">
              {{ store.pendingCount }}
            </span>
          </router-link>
        </div>
        <div class="wordmark">ForgeSight</div>
      </div>
    </nav>
  </div>
</template>

<style scoped>
.layout { min-height: 100dvh; }
.topbar {
  position: fixed; inset: 0 0 auto 0; z-index: 20;
  background: var(--topbar-bg); backdrop-filter: blur(12px);
}
.topbar-inner {
  max-width: 600px; margin: 0 auto; height: 56px; padding: 0 var(--pad);
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
}
.brand { display: flex; align-items: center; gap: 8px; }
.mark {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 9px; color: var(--amber);
  background: var(--halo-bg); border: 1px solid var(--halo-border);
}
.title { font-size: 17px; font-weight: 700; letter-spacing: 0.02em; }
.right { display: flex; align-items: center; gap: 8px; }
.user { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.offline {
  font-size: 11px; color: var(--bad); border: 1px solid rgba(220, 38, 38, 0.4);
  border-radius: 999px; padding: 1px 8px;
}
.ember-line { height: 1px; background: linear-gradient(90deg, transparent, var(--glow-line), transparent); }
.warn {
  max-width: 600px; margin: 0 auto; border-radius: 0; font-size: 12px;
  --n-color: var(--halo-bg) !important;
}
.content {
  max-width: 600px; margin: 0 auto;
  padding: calc(56px + 16px) var(--pad) calc(84px + env(safe-area-inset-bottom) + 16px);
}
.tabbar {
  position: fixed; inset: auto 0 0 0; z-index: 20;
  background: var(--tabbar-bg); backdrop-filter: blur(12px);
  border-top: 1px solid var(--line); padding-bottom: env(safe-area-inset-bottom);
}
.tabbar-inner { max-width: 600px; margin: 0 auto; }
.tabs { display: grid; grid-template-columns: repeat(3, 1fr); }
.tab {
  position: relative; display: flex; flex-direction: column; align-items: center; gap: 3px;
  padding: 9px 0 5px; color: var(--ink-3); transition: color 0.15s;
}
.tab .notch {
  position: absolute; top: 0; width: 26px; height: 2px; border-radius: 0 0 2px 2px;
  background: transparent; transition: background 0.15s;
}
.tab.active { color: var(--amber); }
.tab.active .notch { background: var(--amber); box-shadow: 0 0 10px rgba(245, 158, 11, 0.8); }
.tab .label { font-size: 11px; letter-spacing: 0.04em; }
.tab-badge {
  position: absolute; top: 5px; left: calc(50% + 8px);
  min-width: 16px; height: 16px; padding: 0 4px; border-radius: 8px;
  background: var(--bad); color: #fff; font-size: 10px; line-height: 16px; text-align: center;
  box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.18);
}
.wordmark {
  text-align: center; font-family: var(--mono); font-size: 9px; letter-spacing: 0.34em;
  text-transform: uppercase; color: var(--ink-3); opacity: 0.7; padding-bottom: 6px;
}
</style>
