<script setup lang="ts">
import { computed } from 'vue'
import { NConfigProvider, NDialogProvider, NMessageProvider, darkTheme, lightTheme } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import { useMainStore } from './stores/main.ts'

const store = useMainStore()

const darkOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#F59E0B',
    primaryColorHover: '#FBBF24',
    primaryColorPressed: '#D97706',
    primaryColorSuppl: '#F59E0B',
    successColor: '#22C55E',
    warningColor: '#F59E0B',
    errorColor: '#EF4444',
    borderRadius: '10px',
    fontSize: '14px',
    bodyColor: '#14120F',
    cardColor: '#1C1915',
    modalColor: '#1C1915',
    popoverColor: '#1C1915',
    borderColor: '#2E2822',
    dividerColor: '#241F1A',
    inputColor: '#16130F',
    placeholderColor: '#7A6F62',
    textColorBase: '#F3EDE3',
  },
  Button: { fontWeight: '600' },
  Tag: { borderRadius: '6px' },
}

const lightOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#F59E0B',
    primaryColorHover: '#FBBF24',
    primaryColorPressed: '#D97706',
    primaryColorSuppl: '#F59E0B',
    successColor: '#16A34A',
    warningColor: '#F59E0B',
    errorColor: '#DC2626',
    borderRadius: '10px',
    fontSize: '14px',
    bodyColor: '#F7F4EF',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    borderColor: '#E5DFD5',
    dividerColor: '#EDE8DF',
    inputColor: '#FFFFFF',
    placeholderColor: '#A89B8A',
    textColorBase: '#29231B',
  },
  Button: { fontWeight: '600' },
  Tag: { borderRadius: '6px' },
}

const theme = computed(() => (store.darkMode ? darkTheme : lightTheme))
const themeOverrides = computed(() => (store.darkMode ? darkOverrides : lightOverrides))
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides">
    <n-message-provider placement="bottom">
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
