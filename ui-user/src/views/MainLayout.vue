<template>
  <div class="layout">
    <!-- Top bar -->
    <header class="topbar">
      <span class="app-name">AutoForge</span>
      <n-button quaternary circle @click="showSettings = true">
        <template #icon>
          <n-icon size="20"><settings-outline /></n-icon>
        </template>
      </n-button>
    </header>

    <!-- Content -->
    <main class="content">
      <router-view v-slot="{ Component }">
        <transition name="tab" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
      <div class="footer-brand">ForgeSight</div>
    </main>

    <!-- Bottom nav (mobile) -->
    <nav class="bottom-nav">
      <router-link
        v-for="tab in tabs"
        :key="tab.name"
        :to="{ name: tab.name }"
        class="nav-item"
        :class="{ active: route.name === tab.name }"
      >
        <n-icon size="22"><component :is="tab.icon" /></n-icon>
        <span class="nav-label">{{ tab.label }}</span>
      </router-link>
    </nav>

    <!-- Settings drawer -->
    <n-drawer v-model:show="showSettings" :width="320" placement="right">
      <n-drawer-content title="设置" closable>
        <div class="settings-list">
          <div class="settings-row">
            <span class="settings-label">当前用户</span>
            <span class="settings-value">{{ auth.user?.username || '—' }}</span>
          </div>
          <div class="settings-row">
            <span class="settings-label">角色</span>
            <span class="settings-value">{{ auth.user?.role === 'admin' ? '管理员' : '用户' }}</span>
          </div>
          <n-divider />
          <n-button
            v-if="auth.user?.role === 'admin'"
            tag="a"
            href="/ui/"
            target="_blank"
            block
            style="margin-bottom: 12px"
          >
            打开开发面板
          </n-button>
          <n-button block type="error" secondary @click="handleLogout">
            登出
          </n-button>
        </div>
      </n-drawer-content>
    </n-drawer>

    <!-- Pair request modal -->
    <PairingModal v-if="store.pairRequest" :request="store.pairRequest" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NIcon, NDrawer, NDrawerContent, NDivider, useDialog,
} from 'naive-ui'
import {
  SettingsOutline, PeopleOutline, GitBranchOutline, KeyOutline,
} from '@vicons/ionicons5'
import { useAuthStore } from '@/stores/auth'
import { useMainStore } from '@/stores/main'
import PairingModal from '@/components/PairingModal.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const store = useMainStore()
const dialog = useDialog()

// TODO: remove simulation once SSE /api/mcp/pair-request is wired
onMounted(() => {
  setTimeout(() => {
    store.showPairRequest({
      agent_name_hint: '豆包管家',
      code: '170846',
      expires_at: new Date(Date.now() + 120000).toISOString(),
    })
  }, 2000)
})

const showSettings = ref(false)

const tabs = [
  { name: 'agents', label: 'Agent', icon: PeopleOutline },
  { name: 'automations', label: '自动化', icon: GitBranchOutline },
  { name: 'auth-codes', label: '授权码', icon: KeyOutline },
]

async function handleLogout() {
  showSettings.value = false
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 600px;
  margin: 0 auto;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  padding-top: max(12px, env(safe-area-inset-top));
  background: white;
  border-bottom: 1px solid #f0f0f0;
}
.app-name {
  font-size: 18px;
  font-weight: 700;
  color: #F59E0B;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  padding-bottom: 80px;
}
.bottom-nav {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 100%;
  max-width: 600px;
  display: flex;
  background: white;
  border-top: 1px solid #f0f0f0;
  padding-bottom: env(safe-area-inset-bottom);
}
.nav-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 0;
  text-decoration: none;
  color: #999;
  transition: color 0.15s;
}
.nav-item.active {
  color: #F59E0B;
}
.nav-label {
  font-size: 11px;
  margin-top: 2px;
}
.footer-brand {
  text-align: center;
  font-size: 9px;
  color: #ddd;
  padding: 8px 0 4px;
}
.settings-list {
  padding: 0 4px;
}
.settings-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
}
.settings-label {
  color: #666;
}
.settings-value {
  color: #1a1a1a;
  font-weight: 500;
}

/* Tab transition */
.tab-enter-active, .tab-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.tab-enter-from { opacity: 0; transform: translateY(8px); }
.tab-leave-to { opacity: 0; }
</style>
