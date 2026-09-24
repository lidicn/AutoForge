<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ListChecks,
  Power,
  PowerOff,
  Archive,
  ArchiveRestore,
  Trash2,
  Check,
  X,
  AlertTriangle,
} from 'lucide-vue-next'
import { api } from '../api'
import { useAutomationsStore } from '../stores/automations'
import type { Automation, PendingItem } from '../types/api'

const store = useAutomationsStore()
const filter = ref<'enabled' | 'archived'>('enabled')
const pending = ref<PendingItem[]>([])
const confirmName = ref<string | null>(null)
const rejectId = ref<string | null>(null)
const rejectReason = ref('')

async function loadPending() {
  try {
    const r = await api.listPending()
    pending.value = r.ops || []
  } catch {
    pending.value = []
  }
}
function visibleItems(items: Automation[]) {
  if (filter.value === 'archived') return items.filter((x) => x.archived)
  return items.filter((x) => !x.archived)
}
const filteredGroups = computed(() =>
  store.groups.map((g) => ({ agent: g.agent, items: visibleItems(g.items) })).filter((g) => g.items.length),
)

async function approve(op_id: string) {
  await api.approvePending(op_id)
  await loadPending()
  await store.load()
}
async function reject(op_id: string) {
  await api.rejectPending(op_id, rejectReason.value || '用户驳回')
  rejectId.value = null
  rejectReason.value = ''
  await loadPending()
  await store.load()
}

onMounted(() => {
  store.load()
  loadPending()
})
</script>

<template>
  <div class="px-4 py-4 max-w-2xl mx-auto">
    <div class="flex items-center gap-2 mb-3">
      <ListChecks :size="20" class="text-forge-600" />
      <h2 class="text-lg font-semibold text-ink">自动化</h2>
    </div>

    <!-- 子标签 -->
    <div class="flex gap-1 mb-3 p-1 rounded-xl bg-black/5 w-fit">
      <button
        class="px-4 py-1.5 rounded-lg text-sm font-medium transition"
        :class="filter === 'enabled' ? 'bg-white shadow-card text-ink' : 'text-ink-faint'"
        @click="filter = 'enabled'"
      >
        启用
      </button>
      <button
        class="px-4 py-1.5 rounded-lg text-sm font-medium transition"
        :class="filter === 'archived' ? 'bg-white shadow-card text-ink' : 'text-ink-faint'"
        @click="filter = 'archived'"
      >
        归档
      </button>
    </div>

    <!-- 待审批 -->
    <div v-if="pending.length" class="space-y-2 mb-4">
      <div class="text-xs font-semibold text-ink-faint uppercase tracking-wide">待审批</div>
      <div
        v-for="p in pending"
        :key="p.op_id"
        class="rounded-2xl bg-white shadow-card p-4 border-l-4 border-forge-400"
      >
        <div class="font-medium text-ink">{{ p.payload?.name || p.tool }}</div>
        <div v-if="p.payload?.note" class="text-xs text-ink-soft mt-0.5">{{ p.payload.note }}</div>
        <div v-if="rejectId === p.op_id" class="mt-2">
          <input
            v-model="rejectReason"
            placeholder="驳回理由（可选）"
            class="w-full px-3 py-1.5 rounded-lg border border-black/10 text-sm outline-none focus:ring-2 focus:ring-forge-200"
          />
        </div>
        <div class="flex gap-2 mt-3">
          <button
            class="flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg bg-green-500 text-white text-sm active:scale-95"
            @click="approve(p.op_id)"
          >
            <Check :size="15" /> 批准
          </button>
          <button
            v-if="rejectId !== p.op_id"
            class="flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg bg-black/5 text-ink-soft text-sm active:scale-95"
            @click="rejectId = p.op_id"
          >
            <X :size="15" /> 驳回
          </button>
          <button
            v-else
            class="flex-1 py-1.5 rounded-lg bg-red-500 text-white text-sm active:scale-95"
            @click="reject(p.op_id)"
          >
            确认驳回
          </button>
        </div>
      </div>
    </div>

    <!-- 分组列表 -->
    <div v-if="store.loading" class="text-sm text-ink-faint py-8 text-center">加载中…</div>
    <div v-else-if="store.error" class="text-sm text-red-500 py-8 text-center">{{ store.error }}</div>
    <div v-else-if="!filteredGroups.length" class="text-center text-sm text-ink-faint py-10">
      {{ filter === 'archived' ? '暂无归档自动化' : '暂无启用中的自动化' }}
    </div>
    <div v-else class="space-y-5">
      <div v-for="g in filteredGroups" :key="g.agent">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-sm font-semibold text-ink">{{ g.agent }}</span>
          <span class="text-[11px] text-ink-faint">{{ g.items.length }}</span>
        </div>
        <div class="space-y-2">
          <div
            v-for="a in g.items"
            :key="a.id"
            class="rounded-2xl bg-white shadow-card p-4"
            :class="a.trial.anomaly ? 'ring-1 ring-red-300 animate-pulse-red' : ''"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <div class="font-medium text-ink truncate">{{ a.name }}</div>
                <div class="text-[11px] text-ink-faint mt-0.5">
                  近 7 天触发 {{ a.trigger_7d }} 次 · 最近
                  {{ a.last_triggered ? new Date(a.last_triggered).toLocaleString('zh-CN') : '无记录' }}
                </div>
              </div>
              <span
                class="shrink-0 text-[11px] px-2 py-0.5 rounded-full font-medium"
                :class="{
                  'bg-green-100 text-green-700': a.status === 'enabled',
                  'bg-black/5 text-ink-faint': a.status === 'disabled',
                  'bg-forge-100 text-forge-700': a.status === 'archived',
                }"
              >
                {{ a.status === 'enabled' ? '启用' : a.status === 'disabled' ? '停用' : '归档' }}
              </span>
            </div>

            <p v-if="a.preview_nl" class="text-sm text-ink-soft mt-2 leading-relaxed">{{ a.preview_nl }}</p>

            <div v-if="a.devices.length" class="flex flex-wrap gap-1.5 mt-2">
              <span
                v-for="d in a.devices.slice(0, 4)"
                :key="d.entity_id"
                class="text-[11px] px-2 py-0.5 rounded-md bg-black/5 text-ink-soft"
                >{{ d.friendly_name }}</span
              >
              <span v-if="a.devices.length > 4" class="text-[11px] text-ink-faint"
                >+{{ a.devices.length - 4 }}</span
              >
            </div>

            <div
              v-if="a.trial.anomaly"
              class="flex items-center gap-1 mt-2 text-[11px] text-red-600 font-medium"
            >
              <AlertTriangle :size="13" /> 试演期检测到异常，已暂停
            </div>

            <!-- 操作 -->
            <div class="flex flex-wrap gap-1.5 mt-3">
              <button
                v-if="a.enabled"
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-black/5 text-ink-soft text-xs active:scale-95"
                @click="store.disable(a.name)"
              >
                <PowerOff :size="13" /> 停用
              </button>
              <button
                v-else
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-500 text-white text-xs active:scale-95"
                @click="store.enable(a.name)"
              >
                <Power :size="13" /> 启用
              </button>
              <button
                v-if="!a.archived"
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-black/5 text-ink-soft text-xs active:scale-95"
                @click="store.archive(a.name)"
              >
                <Archive :size="13" /> 归档
              </button>
              <button
                v-else
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-black/5 text-ink-soft text-xs active:scale-95"
                @click="store.unarchive(a.name)"
              >
                <ArchiveRestore :size="13" /> 恢复
              </button>
              <button
                v-if="confirmName !== a.name"
                class="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-black/5 text-red-500 text-xs active:scale-95"
                @click="confirmName = a.name"
              >
                <Trash2 :size="13" /> 删除
              </button>
              <button
                v-else
                class="px-2.5 py-1 rounded-lg bg-red-500 text-white text-xs active:scale-95"
                @click="(store.remove(a.name), (confirmName = null))"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
