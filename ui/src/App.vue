<script setup lang="ts">
import { computed, h } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
  NConfigProvider,
  NMessageProvider,
  NLayout,
  NLayoutHeader,
  NLayoutContent,
  NMenu,
  zhCN,
  dateZhCN,
} from 'naive-ui'
import type { GlobalThemeOverrides, MenuOption } from 'naive-ui'

const route = useRoute()

const menuOptions: MenuOption[] = [
  { label: () => h(RouterLink, { to: '/overview' }, { default: () => '概览' }), key: '/overview' },
  { label: () => h(RouterLink, { to: '/automations' }, { default: () => '自动化列表' }), key: '/automations' },
  { label: () => h(RouterLink, { to: '/running' }, { default: () => '运行中' }), key: '/running' },
  { label: () => h(RouterLink, { to: '/evidence' }, { default: () => '生产态证据' }), key: '/evidence' },
  { label: () => h(RouterLink, { to: '/asks' }, { default: () => '待应答' }), key: '/asks' },
  { label: () => h(RouterLink, { to: '/devices' }, { default: () => '设备目录' }), key: '/devices' },
  { label: () => h(RouterLink, { to: '/simulation' }, { default: () => '仿真回放' }), key: '/simulation' },
  { label: () => h(RouterLink, { to: '/confidence' }, { default: () => '置信度面板' }), key: '/confidence' },
  { label: () => h(RouterLink, { to: '/versions' }, { default: () => '版本与 Diff' }), key: '/versions' },
  { label: () => h(RouterLink, { to: '/spec-editor' }, { default: () => 'AF-Spec 工作台' }), key: '/spec-editor' },
  { label: () => h(RouterLink, { to: '/data' }, { default: () => '数据管理' }), key: '/data' },
  { label: () => h(RouterLink, { to: '/pending' }, { default: () => '待批队列' }), key: '/pending' },
  { label: () => h(RouterLink, { to: '/live' }, { default: () => '真机下发' }), key: '/live' },
  { label: () => h(RouterLink, { to: '/governance' }, { default: () => '治理 / 设置' }), key: '/governance' },
  { label: () => h(RouterLink, { to: '/faults' }, { default: () => '故障注入图鉴' }), key: '/faults' },
  { label: () => h(RouterLink, { to: '/metrics' }, { default: () => '经验闭环' }), key: '/metrics' },
]

const activeKey = computed(() => (route.path.startsWith('/automations') ? '/automations' : route.path))

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#4f46e5',
    primaryColorHover: '#6366f1',
    primaryColorPressed: '#4338ca',
    primaryColorSuppl: '#6366f1',
    borderRadius: '8px',
    fontFamily:
      "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif",
  },
  Card: { borderRadius: '12px' },
  Button: { borderRadiusMedium: '8px' },
}
</script>

<template>
  <n-config-provider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-layout class="app-shell" style="height: 100vh">
        <n-layout-header class="app-header" bordered>
          <div class="brand">
            <div class="brand-mark">AF</div>
            <div class="brand-text">
              <div class="brand-title">AutoForge 控制台</div>
              <div class="brand-sub">Agent 为中心的智能家居自动化平台</div>
            </div>
          </div>
          <n-menu class="app-menu" mode="horizontal" :options="menuOptions" :value="activeKey" />
        </n-layout-header>
        <n-layout-content class="app-content" :native-scrollbar="false">
          <div class="app-container">
            <router-view />
          </div>
        </n-layout-content>
      </n-layout>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
:root {
  --af-bg: #f5f6fa;
}

* {
  box-sizing: border-box;
}

html,
body,
#app {
  margin: 0;
  padding: 0;
  height: 100%;
}

body {
  background: var(--af-bg);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC',
    'Microsoft YaHei', sans-serif;
  color: #1f2937;
}

.app-header {
  display: flex;
  align-items: center;
  gap: 36px;
  height: 64px;
  padding: 0 28px;
  background: #fff;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.brand-mark {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: linear-gradient(135deg, #6366f1, #4338ca);
  color: #fff;
  font-weight: 800;
  font-size: 15px;
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0.5px;
}

.brand-title {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.25;
}

.brand-sub {
  font-size: 11.5px;
  color: #8b93a7;
  line-height: 1.4;
}

.app-menu {
  flex: 1;
  min-width: 0;
}

.app-content {
  background: var(--af-bg);
}

.app-container {
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px 24px 64px;
}
</style>
