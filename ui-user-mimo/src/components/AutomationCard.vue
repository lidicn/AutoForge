<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NPopconfirm, NTag } from 'naive-ui'
import UiIcon from './UiIcon.vue'
import type { Automation } from '../types/api.ts'
import { errorMessage, formatAgo, formatDateTime } from '../logic/format.ts'
import { deviceLabel, statusMeta, trialMeta } from '../logic/automations.ts'

const props = withDefaults(defineProps<{ item: Automation; now: number; view?: 'active' | 'archived' }>(), {
  view: 'active',
})

defineEmits<{
  toggle: [item: Automation]
  archive: [item: Automation]
  restore: [item: Automation]
  remove: [item: Automation]
}>()

const meta = computed(() => statusMeta(props.item.status))
const trial = computed(() => trialMeta(props.item.trial))
const lastLabel = computed(() => formatAgo(props.item.last_triggered, props.now))
</script>

<template>
  <article class="ac">
    <header class="ac-head">
      <h3 class="ac-name">{{ item.name }}</h3>
      <n-tag size="small" :bordered="false" :type="meta.tagType" class="ac-tag">{{ meta.label }}</n-tag>
    </header>

    <section class="blk">
      <div class="micro"><i class="tick" /> 预演效果</div>
      <p class="nl">{{ item.preview_nl }}</p>
    </section>

    <section class="blk">
      <div class="micro"><i class="tick" /> 设备</div>
      <div class="chips">
        <span v-for="d in item.devices" :key="d.entity_id" class="chip" :title="d.entity_id">
          <span class="chip-name">{{ deviceLabel(d) }}</span>
          <span class="chip-id num">{{ d.entity_id }}</span>
        </span>
        <span v-if="!item.devices.length" class="none">无设备</span>
      </div>
    </section>

    <section class="blk">
      <div class="micro">
        <i class="tick" /> 试演期
        <span class="since num">{{ trial.since ? '起于 ' + formatDateTime(trial.since) : '未进入试演' }}</span>
      </div>
      <div class="steps" :class="`level-${trial.level}`">
        <div
          v-for="(s, i) in trial.steps"
          :key="s.key"
          class="step"
          :class="{ on: i <= trial.activeIndex, here: i === trial.activeIndex }"
          :title="s.hint"
        >
          <span class="bar" />
          <span class="lab">{{ s.label }}</span>
        </div>
      </div>
      <p v-if="trial.anomaly" class="anomaly">{{ trial.anomaly }}</p>
    </section>

    <footer class="ac-foot">
      <div class="metas">
        <span>最近触发 <b class="num">{{ lastLabel }}</b></span>
        <span class="dot" />
        <span>近 7 天 <b class="num">{{ item.trigger_7d }}</b> 次</span>
      </div>
      <div class="acts">
        <template v-if="view === 'active'">
          <n-button size="tiny" tertiary @click="$emit('toggle', item)">
            {{ item.status === 'enabled' ? '停用' : '启用' }}
          </n-button>
          <n-button size="tiny" tertiary @click="$emit('archive', item)">归档</n-button>
        </template>
        <template v-else>
          <n-button size="tiny" tertiary @click="$emit('restore', item)">恢复</n-button>
        </template>
        <n-popconfirm @positive-click="$emit('remove', item)" positive-text="删除" negative-text="取消">
          <template #trigger>
            <n-button size="tiny" tertiary type="error">删除</n-button>
          </template>
          删除后不可恢复，确认删除「{{ item.name }}」？
        </n-popconfirm>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.ac {
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  background: var(--bg-elev);
  padding: 12px;
}
.ac + .ac { margin-top: 10px; }
.ac-head { display: flex; align-items: center; gap: 8px; }
.ac-name { margin: 0; font-size: 15px; font-weight: 600; letter-spacing: 0.01em; flex: 1; min-width: 0; }
.ac-tag { flex: none; }
.blk { margin-top: 12px; }
.nl {
  margin: 6px 0 0; padding: 8px 10px; color: var(--ink-2); font-size: 13px;
  border-left: 2px solid rgba(245, 158, 11, 0.55); background: rgba(245, 158, 11, 0.05);
  border-radius: 0 8px 8px 0;
}
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.chip {
  display: inline-flex; align-items: baseline; gap: 6px; max-width: 100%;
  padding: 3px 8px; border: 1px solid var(--line); border-radius: 999px; background: var(--bg-sunken);
}
.chip-name { font-size: 12px; color: var(--ink); }
.chip-id { font-size: 10px; color: var(--ink-3); overflow-wrap: anywhere; }
.none { font-size: 12px; color: var(--ink-3); }
.since { margin-left: 6px; letter-spacing: 0; text-transform: none; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 8px; }
.step { display: flex; flex-direction: column; gap: 5px; }
.step .bar { height: 4px; border-radius: 2px; background: var(--line); }
.step.on .bar { background: linear-gradient(90deg, rgba(245, 158, 11, 0.35), var(--amber)); }
.step.here .bar { box-shadow: 0 0 10px rgba(245, 158, 11, 0.55); animation: pulse 1.8s ease-in-out infinite; }
.step .lab { font-size: 11px; color: var(--ink-3); }
.step.here .lab { color: var(--amber-2); }
.level-anomaly .step.on .bar { background: linear-gradient(90deg, rgba(239, 68, 68, 0.35), var(--bad)); }
.level-anomaly .step.here .bar { box-shadow: 0 0 10px rgba(239, 68, 68, 0.55); animation: none; }
.anomaly {
  margin: 8px 0 0; padding: 6px 9px; border-radius: 8px; font-size: 12px;
  color: var(--bad); background: rgba(220, 38, 38, 0.08); border: 1px solid rgba(220, 38, 38, 0.25);
}
.ac-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--line-soft);
}
.metas { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-3); }
.metas b { color: var(--ink-2); font-weight: 600; }
.metas .dot { width: 3px; height: 3px; border-radius: 50%; background: var(--ink-3); }
.acts { display: flex; gap: 6px; }
@keyframes pulse { 0%, 100% { opacity: 0.6 } 50% { opacity: 1 } }
</style>