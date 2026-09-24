<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { NButton, NEmpty, useMessage } from 'naive-ui'
import UiIcon from '../components/UiIcon.vue'
import AutomationCard from '../components/AutomationCard.vue'
import type { Automation, PendingItem } from '../types/api.ts'
import { useMainStore } from '../stores/main.ts'
import { blastMeta } from '../logic/automations.ts'
import { errorMessage, formatAgo } from '../logic/format.ts'

const store = useMainStore()
const message = useMessage()

const tab = ref<'enabled' | 'archived'>('enabled')
const folded = reactive<Record<string, boolean>>({})

const groups = computed(() => (tab.value === 'enabled' ? store.enabledGroups : store.archivedGroups))
const blast = (r: string) => blastMeta(r)

function toggleFold (id: string) { folded[id] = !folded[id] }

async function onToggle (item: Automation) {
  const to = item.status === 'enabled' ? '已停用' : '已启用'
  try { await store.toggleAutomation(item); message.success(`${item.name} ${to}`) } catch (e) { message.error(errorMessage(e)) }
}
async function onArchive (item: Automation) {
  try { await store.archiveAutomation(item, true); message.success(`已归档：${item.name}`) } catch (e) { message.error(errorMessage(e)) }
}
async function onRestore (item: Automation) {
  try { await store.archiveAutomation(item, false); message.success(`已恢复：${item.name}`) } catch (e) { message.error(errorMessage(e)) }
}
async function onRemove (item: Automation) {
  try { await store.deleteAutomation(item.id); message.success(`已删除：${item.name}`) } catch (e) { message.error(errorMessage(e)) }
}
async function onApprove (p: PendingItem) {
  try { await store.approvePending(p.op_id); message.success('已批准该操作') } catch (e) { message.error(errorMessage(e)) }
}
async function onReject (p: PendingItem) {
  try { await store.rejectPending(p.op_id); message.success('已驳回该操作') } catch (e) { message.error(errorMessage(e)) }
}
</script>

<template>
  <div class="autos">
    <div class="subtabs">
      <button class="subtab" :class="{ on: tab === 'enabled' }" @click="tab = 'enabled'">
        启用
        <span v-if="store.pendingCount" class="badge">{{ store.pendingCount }}</span>
      </button>
      <button class="subtab" :class="{ on: tab === 'archived' }" @click="tab = 'archived'">
        归档 <span class="num small">{{ store.archivedCount }}</span>
      </button>
    </div>

    <section v-if="tab === 'enabled' && store.pendings.length" class="pending">
      <header class="pending-head">
        <span class="badge big num">{{ store.pendingCount }}</span>
        <h2>待批操作</h2>
        <span class="tip">批准后才会真正生效</span>
      </header>
      <ul class="pend-list">
        <li v-for="p in store.pendings" :key="p.op_id" class="card pend">
          <div class="top">
            <span class="agent">{{ p.agent_name }}</span>
            <span class="blast" :class="blast(p.blast_radius).level">{{ blast(p.blast_radius).short }}</span>
          </div>
          <p class="summary">{{ p.summary }}</p>
          <p class="radius">影响面：{{ p.blast_radius }}</p>
          <div class="bottom">
            <span class="num time">{{ formatAgo(p.submitted_at, store.now) }} 提交</span>
            <div class="acts">
              <n-button size="tiny" type="primary" @click="onApprove(p)">批准</n-button>
              <n-button size="tiny" secondary @click="onReject(p)">驳回</n-button>
            </div>
          </div>
        </li>
      </ul>
    </section>

    <section v-for="g in groups" :key="g.agent_id" class="card group">
      <header class="group-head" @click="toggleFold(g.agent_id)">
        <ui-icon name="chevron" :size="16" class="chev" :class="{ open: !folded[g.agent_id] }" />
        <h3>{{ g.agent_name }}</h3>
        <span class="num small">{{ g.items.length }} 条</span>
      </header>
      <div v-show="!folded[g.agent_id]" class="group-body">
        <automation-card
          v-for="it in g.items"
          :key="it.id"
          :item="it"
          :view="tab"
          :now="store.now"
          @toggle="onToggle"
          @archive="onArchive"
          @restore="onRestore"
          @remove="onRemove"
        />
      </div>
    </section>

    <n-empty
      v-if="!groups.length"
      :description="tab === 'enabled' ? '暂无启用中的自动化' : '归档是空的'"
      size="small"
    />
  </div>
</template>

<style scoped>
.subtabs { display: flex; gap: 8px; margin-bottom: 14px; }
.subtab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 14px; border-radius: 999px; font-size: 13px; cursor: pointer;
  color: var(--ink-2); background: var(--bg-sunken); border: 1px solid var(--line); transition: 0.15s;
}
.subtab.on {
  color: var(--amber); border-color: rgba(245, 158, 11, 0.45); background: rgba(245, 158, 11, 0.08);
}
.small { font-size: 11px; color: var(--ink-3); }
.pending {
  margin-bottom: 16px; padding: 12px; border-radius: var(--radius);
  border: 1px solid rgba(239, 68, 68, 0.28); background: rgba(239, 68, 68, 0.06);
}
.pending-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.pending-head h2 { margin: 0; font-size: 13px; font-weight: 700; letter-spacing: 0.06em; }
.pending-head .tip { margin-left: auto; font-size: 11px; color: var(--ink-3); }
.badge {
  min-width: 17px; height: 17px; padding: 0 5px; border-radius: 9px; background: var(--bad);
  color: #fff; font-family: var(--mono); font-size: 11px; line-height: 17px; text-align: center;
}
.badge.big { min-width: 22px; height: 22px; line-height: 22px; font-size: 12px; border-radius: 11px; }
.pend-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.pend { background: var(--bg-elev); padding: 12px; }
.top { display: flex; align-items: center; gap: 8px; }
.agent { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
.blast {
  margin-left: auto; padding: 1px 8px; border-radius: 999px; font-size: 11px;
  border: 1px solid var(--line); color: var(--ink-2);
}
.blast.medium { color: var(--amber-2); border-color: rgba(245, 158, 11, 0.4); background: rgba(245, 158, 11, 0.08); }
.blast.high, .blast.critical { color: var(--bad); border-color: rgba(220, 38, 38, 0.45); background: rgba(220, 38, 38, 0.08); }
.summary { margin: 8px 0 0; font-size: 14px; }
.radius { margin: 6px 0 0; font-size: 11px; color: var(--ink-3); }
.bottom { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; }
.time { font-size: 11px; color: var(--ink-3); }
.acts { display: flex; gap: 6px; }
.group { padding: 0; overflow: hidden; margin-bottom: 12px; }
.group + .group { margin-top: 0; }
.group-head {
  display: flex; align-items: center; gap: 8px; padding: 12px 14px; cursor: pointer;
  border-bottom: 1px solid var(--line-soft);
}
.group-head h3 { margin: 0; flex: 1; font-size: 14px; font-weight: 600; }
.chev { color: var(--ink-3); transition: transform 0.18s; }
.chev.open { transform: rotate(180deg); }
.group-body { padding: 12px; }
</style>