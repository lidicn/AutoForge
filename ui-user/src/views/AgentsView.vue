<template>
  <div class="agents-view">
    <h2 class="section-title">Agent</h2>

    <!-- Connect new agent: MCP URL -->
    <div class="mcp-card">
      <div class="mcp-label">新 Agent 连接</div>
      <p class="mcp-hint">把下面的 MCP 地址告诉 agent，agent 连入后会弹出配对码</p>
      <div class="mcp-url-row">
        <span class="mcp-url">{{ mcpUrl }}</span>
        <n-button size="small" type="primary" secondary @click="copyMcpUrl">
          复制
        </n-button>
      </div>
    </div>

    <h3 class="sub-title">已连接</h3>

    <div v-if="store.agents.length === 0" class="empty-state">
      <n-icon size="48" color="#ddd"><people-outline /></n-icon>
      <p>还没有已连接的 Agent</p>
    </div>

    <div
      v-for="agent in store.agents"
      :key="agent.agent_id"
      class="agent-card"
    >
      <div class="agent-info">
        <div class="agent-avatar">
          <n-icon size="22" color="#F59E0B"><person-outline /></n-icon>
        </div>
        <div class="agent-meta">
          <div class="agent-name-row">
            <span class="agent-name">{{ agent.name }}</span>
            <n-button text size="small" @click="startRename(agent)">
              <n-icon size="14"><create-outline /></n-icon>
            </n-button>
          </div>
          <span class="agent-lastseen">最近活跃：{{ agent.last_seen }}</span>
        </div>
      </div>
      <n-button size="small" type="error" secondary @click="handleDelete(agent)">
        删除配对
      </n-button>
    </div>

    <!-- Rename modal -->
    <n-modal v-model:show="showRename" preset="card" title="重命名 Agent" style="max-width: 320px">
      <n-input v-model:value="renameValue" placeholder="新名称" autofocus />
      <div style="margin-top: 16px; display: flex; gap: 8px; justify-content: flex-end">
        <n-button @click="showRename = false">取消</n-button>
        <n-button type="primary" @click="confirmRename">确认</n-button>
      </div>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NIcon, NButton, NModal, NInput, useDialog, useMessage,
} from 'naive-ui'
import {
  PeopleOutline, PersonOutline, CreateOutline,
} from '@vicons/ionicons5'
import { useMainStore } from '@/stores/main'
import type { Agent } from '@/types/api'

const store = useMainStore()
const dialog = useDialog()
const message = useMessage()

const mcpUrl = 'http://192.168.2.200:8000/mcp'

async function copyMcpUrl() {
  try {
    await navigator.clipboard.writeText(mcpUrl)
    message.success('已复制 MCP 地址')
  } catch {
    message.error('复制失败，请手动复制')
  }
}

const showRename = ref(false)
const renameValue = ref('')
const editingAgentId = ref('')

onMounted(() => {
  store.fetchAgents()
})

function startRename(agent: Agent) {
  renameValue.value = agent.name
  editingAgentId.value = agent.agent_id
  showRename.value = true
}

async function confirmRename() {
  if (!renameValue.value.trim()) return
  await store.renameAgent(editingAgentId.value, renameValue.value.trim())
  showRename.value = false
  message.success('已重命名')
}

function handleDelete(agent: Agent) {
  dialog.warning({
    title: '删除配对',
    content: `确定要断开 ${agent.name} 吗？该 Agent 将无法再访问你的自动化。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      await store.deleteAgent(agent.agent_id)
      message.success('已删除')
    },
  })
}
</script>

<style scoped>
.agents-view {
  padding: 0;
}
.section-title {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 16px;
}
.sub-title {
  font-size: 14px;
  font-weight: 600;
  color: #999;
  margin: 20px 0 12px;
}
.mcp-card {
  background: #fef3c7;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 8px;
}
.mcp-label {
  font-size: 14px;
  font-weight: 600;
  color: #92400e;
  margin-bottom: 4px;
}
.mcp-hint {
  font-size: 13px;
  color: #92400e;
  opacity: 0.8;
  margin-bottom: 10px;
}
.mcp-url-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mcp-url {
  flex: 1;
  font-family: monospace;
  font-size: 13px;
  color: #92400e;
  background: rgba(255,255,255,0.6);
  padding: 6px 10px;
  border-radius: 6px;
  word-break: break-all;
}
.agent-card {
  background: white;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.agent-info {
  display: flex;
  align-items: center;
  gap: 12px;
}
.agent-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #fef3c7;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.agent-name-row {
  display: flex;
  align-items: center;
  gap: 4px;
}
.agent-name {
  font-size: 16px;
  font-weight: 600;
}
.agent-lastseen {
  font-size: 13px;
  color: #999;
}
.empty-state {
  text-align: center;
  padding: 60px 0;
  color: #999;
}
.empty-state p {
  margin-top: 12px;
}
</style>
