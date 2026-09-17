<script setup lang="ts">
import { computed } from 'vue'
import { NAlert } from 'naive-ui'
import type { Diagnostic } from '@/types/api'

const props = defineProps<{ diagnostics: Diagnostic[] }>()

const alerts = computed(() =>
  props.diagnostics.filter((d) =>
    ['L3_ACTION', 'HTTP_NOT_WHITELISTED', 'SHADOW_WRITES_DEVICE', 'LOW_CONF_WRITES_DEVICE', 'L2_NEEDS_CONFIRM'].includes(
      d.code,
    ),
  ),
)

const summary = computed(() =>
  alerts.value.length > 0
    ? `${alerts.value.length} 个安全闸门问题：${alerts.value.map((a) => a.code).join('、')}`
    : '',
)
</script>

<template>
  <n-alert v-if="summary" type="error" title="⚠️ 安全闸门触发" style="margin-bottom: 16px">
    <p style="margin: 0 0 8px">{{ summary }}</p>
    <ul style="margin: 0; padding-left: 18px">
      <li v-for="a in alerts" :key="a.code + a.node_id" style="margin-bottom: 4px">
        <code>{{ a.code }}</code>
        <span v-if="a.automation_id">[{{ a.automation_id }}]</span>
        {{ a.message }}
      </li>
    </ul>
  </n-alert>
</template>
