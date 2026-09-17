<script setup lang="ts">
import { computed } from 'vue'
import { NCard } from 'naive-ui'
import type { Diagnostic } from '@/types/api'

const props = defineProps<{ diagnostics: Diagnostic[] }>()

const errors = computed(() => props.diagnostics.filter((d) => d.level === 'error'))
const warnings = computed(() => props.diagnostics.filter((d) => d.level === 'warning'))

function loc(d: Diagnostic) {
  return [d.automation_id, d.node_id].filter(Boolean).join('/')
}
</script>

<template>
  <n-card title="扫描诊断" :bordered="false" class="diag-card">
    <template v-if="diagnostics.length">
      <div v-if="errors.length" class="group">
        <div class="group-title error">🔴 错误 ({{ errors.length }})</div>
        <div v-for="d in errors" :key="d.code + d.node_id + d.message" class="item error">
          <code class="code">{{ d.code }}</code>
          <span v-if="loc(d)" class="loc">{{ loc(d) }}</span>
          <span class="msg">{{ d.message }}</span>
        </div>
      </div>
      <div v-if="warnings.length" class="group">
        <div class="group-title warning">🟡 警告 ({{ warnings.length }})</div>
        <div v-for="d in warnings" :key="d.code + d.node_id + d.message" class="item warning">
          <code class="code">{{ d.code }}</code>
          <span v-if="loc(d)" class="loc">{{ loc(d) }}</span>
          <span class="msg">{{ d.message }}</span>
        </div>
      </div>
    </template>
    <div v-else class="ok">✓ 无诊断问题</div>
  </n-card>
</template>

<style scoped>
.diag-card {
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.group {
  margin-bottom: 12px;
}
.group-title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
}
.group-title.error {
  color: #dc2626;
}
.group-title.warning {
  color: #d97706;
}
.item {
  padding: 9px 12px;
  border-radius: 8px;
  margin-bottom: 5px;
  font-size: 13px;
}
.item.error {
  background: #fef2f2;
  border-left: 3px solid #dc2626;
}
.item.warning {
  background: #fffbeb;
  border-left: 3px solid #f59e0b;
}
.code {
  font-family: monospace;
  font-weight: 700;
  font-size: 12px;
  margin-right: 8px;
}
.loc {
  color: #8b93a7;
  font-family: monospace;
  font-size: 12px;
  margin-right: 8px;
}
.ok {
  color: #10b981;
  font-size: 14px;
}
</style>
