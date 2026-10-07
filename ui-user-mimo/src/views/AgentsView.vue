<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NEmpty, NInput, NPopconfirm, NSpin, useMessage } from 'naive-ui'
import { useClipboard } from '@vueuse/core'
import UiIcon from '../components/UiIcon.vue'
import PairingModal from '../components/PairingModal.vue'
import type { Agent } from '../types/api.ts'
import { useMainStore } from '../stores/main.ts'
import { MCP_URL } from '../api/env.ts'
import { errorMessage, formatAgo, formatDateTime } from '../logic/format.ts'

const store = useMainStore()
const message = useMessage()
const { copy, copied } = useClipboard({ legacy: true })

const showPair = ref(false)
// SSE 推码后自动弹出配对弹窗（真实流程无需手动生成）
watch(() => store.pair, (v) => { if (v) showPair.value = true })
const editingId = ref<string | null>(null)
const editingName = ref('')

async function copyUrl () {
  try {
    await copy(MCP_URL)
    message.success('MCP 地址已复制')
  } catch {
    message.error('复制失败，请手动选择地址')
  }
}

function startRename (a: Agent) {
  editingId.value = a.agent_id
  editingName.value = a.name
}

async function saveRename (a: Agent) {
  const next = editingName.value.trim()
  if (!next) { message.warning('名称不能为空'); return }
  if (next === a.name) { editingId.value = null; return }
  try {
    await store.renameAgent(a.agent_id, next)
    message.success(`已重命名为「${next}」`)
    editingId.value = null
  } catch (e) { message.error(errorMessage(e)) }
}

async function removeAgent (a: Agent) {
  try {
    await store.deleteAgent(a.agent_id)
    message.success(`已删除配对：${a.name}`)
  } catch (e) { message.error(errorMessage(e)) }
}

function onPaired (name: string) { message.success(`新 Agent「${name}」已加入列表`) }
</script>

<template>
  <div class="agents">
    <section class="card mcp">
      <div class="micro"><i class="tick" /> MCP 连接</div>
      <div class="mcp-row">
        <code class="url num">{{ MCP_URL }}</code>
        <n-button size="small" secondary class="copy" @click="copyUrl">
          <template #icon>
            <ui-icon :name="copied ? 'check' : 'copy'" :size="15" />
          </template>
          {{ copied ? '已复制' : '复制' }}
        </n-button>
      </div>
      <p class="hint">Agent 通过该 MCP 端点接入 AutoForge；配对成功后才会获得授权码。</p>
    </section>

    <header class="row">
      <h2 class="sec-title">Agent 列表 <span class="num count">{{ store.agents.length }}</span></h2>
      <n-button type="primary" size="small" @click="showPair = true">
        <template #icon><ui-icon name="plus" :size="15" /></template>
        配对新 Agent
      </n-button>
    </header>

    <n-spin :show="store.loading && !store.agents.length">
      <ul v-if="store.agents.length" class="list">
        <li v-for="a in store.agents" :key="a.agent_id" class="card agent">
          <div class="head">
            <div v-if="editingId === a.agent_id" class="rename">
              <n-input
                v-model:value="editingName"
                size="small"
                :maxlength="24"
                placeholder="Agent 名称"
                @keydown.enter.prevent="saveRename(a)"
                @keydown.esc="editingId = null"
              />
              <n-button size="tiny" type="primary" @click="saveRename(a)">保存</n-button>
              <n-button size="tiny" secondary @click="editingId = null">取消</n-button>
            </div>
            <template v-else>
              <h3 class="name">{{ a.name }}</h3>
              <div class="acts">
                <button class="icon-btn" title="重命名" @click="startRename(a)">
                  <ui-icon name="pencil" :size="16" />
                </button>
                <n-popconfirm @positive-click="removeAgent(a)" positive-text="删除配对" negative-text="取消">
                  <template #trigger>
                    <button class="icon-btn danger" title="删除配对">
                      <ui-icon name="trash" :size="16" />
                    </button>
                  </template>
                  删除配对后，该 Agent 名下的自动化会被归档（不物理删除）。确认？
                </n-popconfirm>
              </div>
            </template>
          </div>

          <dl class="metas">
            <div><dt>最后活跃</dt><dd class="num">{{ formatAgo(a.last_seen, store.now) }}</dd></div>
            <div><dt>配对于</dt><dd class="num">{{ formatDateTime(a.connected_at) }}</dd></div>
            <div><dt>ID</dt><dd class="num id">{{ a.agent_id }}</dd></div>
          </dl>
        </li>
      </ul>
      <n-empty v-else-if="!store.loading" description="还没有 Agent，点右上角「配对新 Agent」" size="small" />
    </n-spin>

    <pairing-modal v-model:show="showPair" @paired="onPaired" />
  </div>
</template>

<style scoped>
.mcp-row {
  display: flex; align-items: center; gap: 10px; margin-top: 10px;
}
.url {
  flex: 1; min-width: 0; padding: 10px 12px; border-radius: 10px;
  background: var(--bg-sunken); border: 1px solid var(--line);
  font-size: 13px; color: var(--amber-2); overflow-wrap: anywhere;
}
.copy { flex: none; }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--ink-3); }
.row {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin: 20px 0 10px;
}
.sec-title { margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; color: var(--ink-2); }
.count { color: var(--ink-3); font-size: 12px; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.agent { padding: 12px 14px; }
.head { display: flex; align-items: center; gap: 8px; }
.name { margin: 0; flex: 1; min-width: 0; font-size: 16px; font-weight: 600; }
.acts { display: flex; gap: 6px; }
.rename { display: flex; gap: 6px; width: 100%; }
.rename :deep(.n-input) { flex: 1; }
.metas { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 10px 0 0; }
.metas > div { display: flex; align-items: baseline; gap: 6px; }
.metas dt { font-size: 11px; color: var(--ink-3); }
.metas dd { margin: 0; font-size: 12px; color: var(--ink-2); }
.metas dd.id { color: var(--ink-3); overflow-wrap: anywhere; }
</style>