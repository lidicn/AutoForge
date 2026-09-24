<template>
  <div class="automations-view">
    <!-- Sub tabs -->
    <div class="sub-tabs">
      <button
        class="sub-tab"
        :class="{ active: subTab === 'active' }"
        @click="subTab = 'active'"
      >
        启用
        <span v-if="pendingCount > 0" class="badge">{{ pendingCount }}</span>
      </button>
      <button
        class="sub-tab"
        :class="{ active: subTab === 'archived' }"
        @click="subTab = 'archived'"
      >
        归档
      </button>
    </div>

    <!-- Grouped by agent -->
    <template v-if="subTab === 'active'">
      <div v-for="(group, agentName) in activeGroups" :key="agentName" class="group">
        <div class="group-header" @click="toggleGroup(agentName)">
          <n-icon size="16" class="chevron" :class="{ open: !collapsedGroups.has(agentName) }">
            <chevron-down-outline />
          </n-icon>
          <span class="group-name">{{ agentName }}</span>
          <span class="group-count">{{ group.length }}</span>
        </div>
        <div v-show="!collapsedGroups.has(agentName)">
          <AutomationCard
            v-for="auto in group"
            :key="auto.id"
            :auto="auto"
            @toggle="(enable) => handleToggle(auto.id, enable)"
            @archive="(arch) => handleArchive(auto.id, arch)"
            @remove="handleDelete(auto.id)"
            @approve="handleApprove(auto.id)"
            @reject="(reason) => handleReject(auto.id, reason)"
          />
        </div>
      </div>
    </template>

    <!-- Archived -->
    <template v-else>
      <div v-if="archivedList.length === 0" class="empty-state">
        <p>暂无已归档的自动化</p>
      </div>
      <AutomationCard
        v-for="auto in archivedList"
        :key="auto.id"
        :auto="auto"
        @toggle="(enable) => handleToggle(auto.id, enable)"
        @archive="(arch) => handleArchive(auto.id, arch)"
        @remove="handleDelete(auto.id)"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NIcon, useMessage, useDialog } from 'naive-ui'
import { ChevronDownOutline } from '@vicons/ionicons5'
import { useMainStore } from '@/stores/main'
import AutomationCard from '@/components/AutomationCard.vue'
import type { Automation } from '@/types/api'

const store = useMainStore()
const message = useMessage()
const dialog = useDialog()

const subTab = ref<'active' | 'archived'>('active')
const collapsedGroups = ref<Set<string>>(new Set())

onMounted(() => {
  store.fetchAutomations()
})

const activeList = computed(() =>
  store.automations.filter(a => !a.archived)
)

const archivedList = computed(() =>
  store.automations.filter(a => a.archived)
)

const activeGroups = computed(() => {
  const groups: Record<string, Automation[]> = {}
  for (const auto of activeList.value) {
    const key = auto.agent_name || '未归属/本地'
    if (!groups[key]) groups[key] = []
    groups[key].push(auto)
  }
  return groups
})

const pendingCount = computed(() =>
  activeList.value.filter(a => a.status === 'pending').length
)

function toggleGroup(name: string) {
  if (collapsedGroups.value.has(name)) {
    collapsedGroups.value.delete(name)
  } else {
    collapsedGroups.value.add(name)
  }
}

async function handleToggle(id: string, enable: boolean) {
  await store.toggleAutomation(id, enable)
  message.success(enable ? '已启用' : '已禁用')
}

async function handleArchive(id: string, archive: boolean) {
  await store.archiveAutomation(id, archive)
  message.success(archive ? '已归档' : '已恢复')
}

function handleDelete(id: string) {
  dialog.warning({
    title: '删除自动化',
    content: '确定要删除这条自动化吗？此操作不可撤销。',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      await store.deleteAutomation(id)
      message.success('已删除')
    },
  })
}

async function handleApprove(id: string) {
  await store.toggleAutomation(id, true)
  message.success('已批准，自动化生效')
}

async function handleReject(id: string, reason: string) {
  await store.deleteAutomation(id)
  message.success(`已驳回：${reason}`)
}
</script>

<style scoped>
.automations-view {
  padding: 0;
}
.sub-tabs {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
  border-bottom: 1px solid #eee;
  padding-bottom: 8px;
}
.sub-tab {
  background: none;
  border: none;
  font-size: 15px;
  color: #999;
  cursor: pointer;
  padding: 4px 0;
  position: relative;
}
.sub-tab.active {
  color: #F59E0B;
  font-weight: 600;
}
.sub-tab.active::after {
  content: '';
  position: absolute;
  bottom: -9px;
  left: 0;
  right: 0;
  height: 2px;
  background: #F59E0B;
  border-radius: 1px;
}
.badge {
  display: inline-block;
  background: #ef4444;
  color: white;
  font-size: 11px;
  padding: 0 6px;
  border-radius: 10px;
  margin-left: 4px;
}
.group {
  margin-bottom: 16px;
}
.group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 4px;
  cursor: pointer;
}
.chevron {
  transition: transform 0.2s;
}
.chevron.open {
  transform: rotate(0deg);
}
.group-name {
  font-size: 14px;
  font-weight: 600;
  color: #555;
}
.group-count {
  font-size: 12px;
  color: #999;
}
.empty-state {
  text-align: center;
  padding: 40px 0;
  color: #999;
}
</style>
