<template>
  <div
    class="auto-card"
    :class="{ 'anomaly-pulse': auto.status === 'anomaly' }"
  >
    <div class="card-top">
      <span class="auto-name">{{ auto.name }}</span>
      <n-tag :type="statusTag.type" size="small" :bordered="false">
        {{ statusTag.label }}
      </n-tag>
    </div>

    <p class="auto-preview">{{ auto.preview_nl }}</p>

    <div class="auto-devices">
      <n-tag
        v-for="d in auto.devices"
        :key="d.entity_id"
        size="tiny"
        style="margin-right: 4px; margin-bottom: 4px"
        @click="showDeviceDetail(d)"
      >
        {{ d.friendly_name }}
      </n-tag>
    </div>

    <div v-if="auto.last_triggered" class="auto-meta">
      最近触发：{{ auto.last_triggered }} · 近7天 {{ auto.trigger_7d }} 次
    </div>

    <!-- Trial state -->
    <div v-if="auto.trial" class="trial-bar" :class="auto.trial.state">
      <n-icon size="14">
        <flash-outline v-if="auto.trial.state === 'canary'" />
        <eye-outline v-else />
      </n-icon>
      <span>
        试演期 · {{ trialLabel }}
        <template v-if="auto.trial.anomaly">（{{ auto.trial.anomaly }}）</template>
      </span>
    </div>

    <!-- Actions -->
    <div class="card-actions">
      <template v-if="auto.status === 'pending'">
        <n-button type="primary" size="small" @click="onApprove">批准</n-button>
        <n-button size="small" @click="onReject">驳回</n-button>
      </template>
      <template v-else-if="!auto.archived">
        <n-button
          v-if="auto.status === 'enabled'"
          size="small" secondary
          @click="$emit('toggle', false)"
        >禁用</n-button>
        <n-button
          v-else
          size="small" type="primary" secondary
          @click="$emit('toggle', true)"
        >启用</n-button>
        <n-button size="small" secondary @click="$emit('archive', true)">归档</n-button>
      </template>
      <template v-else>
        <n-button size="small" type="primary" secondary @click="$emit('archive', false)">恢复</n-button>
      </template>
      <n-button size="small" type="error" secondary @click="$emit('remove')">删除</n-button>
    </div>

    <!-- Device detail modal -->
    <n-modal v-model:show="showDevices" preset="card" title="涉及设备" style="max-width: 340px">
      <div v-for="d in auto.devices" :key="d.entity_id" class="device-row">
        <span class="device-friendly">{{ d.friendly_name }}</span>
        <span class="device-entity">{{ d.entity_id }}</span>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { NTag, NButton, NIcon, NModal } from 'naive-ui'
import { FlashOutline, EyeOutline } from '@vicons/ionicons5'
import type { Automation, DeviceRef, AutomationStatus } from '@/types/api'

const props = defineProps<{ auto: Automation }>()
const emit = defineEmits<{
  toggle: [enable: boolean]
  archive: [archive: boolean]
  remove: []
  approve: []
  reject: [reason: string]
}>()
const showDevices = ref(false)

const statusMap: Record<AutomationStatus, { label: string; type: 'success' | 'warning' | 'default' | 'error' }> = {
  pending: { label: '待批', type: 'warning' },
  enabled: { label: '启用', type: 'success' },
  disabled: { label: '禁用', type: 'default' },
  anomaly: { label: '异常暂停', type: 'error' },
}

const statusTag = computed(() => statusMap[props.auto.status])

const trialLabel = computed(() => {
  if (!props.auto.trial) return ''
  const map = { auto: '正式运行', shadow: '影子模式', canary: '灰度' }
  return map[props.auto.trial.state]
})

function showDeviceDetail(d: DeviceRef) {
  showDevices.value = true
}

function onApprove() {
  emit('approve')
}

function onReject() {
  const reason = prompt('请输入驳回理由：')
  if (reason !== null) emit('reject', reason)
}
</script>

<style scoped>
.auto-card {
  background: white;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  border: 2px solid transparent;
}
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.auto-name {
  font-size: 16px;
  font-weight: 600;
}
.auto-preview {
  font-size: 14px;
  color: #555;
  margin-bottom: 10px;
  line-height: 1.5;
}
.auto-devices {
  margin-bottom: 8px;
}
.auto-meta {
  font-size: 12px;
  color: #999;
  margin-bottom: 8px;
}
.trial-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 6px;
  margin-bottom: 10px;
}
.trial-bar.shadow { background: #f3f4f6; color: #6b7280; }
.trial-bar.canary { background: #fef3c7; color: #92400e; }
.trial-bar.auto { background: #dcfce7; color: #166534; }
.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.device-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}
.device-entity {
  font-family: monospace;
  font-size: 12px;
  color: #999;
}
</style>
