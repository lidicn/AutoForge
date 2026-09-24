<script setup lang="ts">
import { ref } from 'vue'
import { Trash2, Users, Info, CheckCircle2 } from 'lucide-vue-next'
import { useAgentsStore } from '../stores/agents'

const agentsStore = useAgentsStore()
const confirmId = ref<string | null>(null)

async function doDelete(id: string) {
  await agentsStore.remove(id)
  confirmId.value = null
}
</script>

<template>
  <div class="px-4 py-4 max-w-2xl mx-auto">
    <div class="flex items-center gap-2 mb-3">
      <Users :size="20" class="text-forge-600" />
      <h2 class="text-lg font-semibold text-ink">已连接的 Agent</h2>
    </div>

    <div class="flex gap-2 rounded-xl bg-forge-50 border border-forge-100 p-3 mb-4 text-sm text-forge-700">
      <Info :size="16" class="mt-0.5 shrink-0" />
      <p>Agent 通过 MCP 发起连接后，配对码会自动出现在本设备弹窗——无需手动生成。把码口述给 Agent 即可完成配对。</p>
    </div>

    <div v-if="agentsStore.loading" class="text-sm text-ink-faint py-8 text-center">加载中…</div>
    <div v-else-if="agentsStore.error" class="text-sm text-red-500 py-8 text-center">{{ agentsStore.error }}</div>
    <ul v-else-if="agentsStore.agents.length" class="space-y-2">
      <li
        v-for="a in agentsStore.agents"
        :key="a.id"
        class="flex items-center justify-between rounded-2xl bg-white shadow-card p-4"
      >
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <CheckCircle2 :size="16" class="text-green-500 shrink-0" />
            <span class="font-medium text-ink truncate">{{ a.name }}</span>
          </div>
          <div class="text-[11px] text-ink-faint mt-0.5">{{ a.scopes.join(' · ') }}</div>
        </div>
        <button
          v-if="confirmId !== a.id"
          class="text-ink-faint hover:text-red-500 active:scale-95 p-2"
          @click="confirmId = a.id"
          aria-label="删除配对"
        >
          <Trash2 :size="18" />
        </button>
        <div v-else class="flex items-center gap-2">
          <button class="text-xs text-ink-faint" @click="confirmId = null">取消</button>
          <button
            class="text-xs px-3 py-1.5 rounded-lg bg-red-500 text-white active:scale-95"
            @click="doDelete(a.id)"
          >
            确认删除
          </button>
        </div>
      </li>
    </ul>
    <div v-else class="text-center text-sm text-ink-faint py-10">暂无已配对 Agent</div>
  </div>
</template>
