<script setup lang="ts">
import { computed } from 'vue'
import { NInput, NSelect, NSlider, NSpace, NText } from 'naive-ui'
import type { AskControlMeta } from '@/types/api'

const props = defineProps<{
  /** 后端 AskSpec.control() 产出的控件元数据——映射的唯一真相，前端只做渲染 */
  control: AskControlMeta
  modelValue: unknown
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: unknown): void
}>()

const widget = computed(() => props.control.widget ?? 'input')

const options = computed(() => (props.control.options ?? []).map((o) => ({ label: o, value: o })))
const min = computed(() => props.control.min ?? 0)
const max = computed(() => props.control.max ?? 100)
const unit = computed(() => props.control.unit ?? '')

// ── time_range：内部一律用分钟 [0,1439]，仅在呈现层转 HH:MM ──
function fmtMin(m: unknown): string {
  const n = Number(m)
  if (!Number.isFinite(n)) return ''
  const v = Math.max(0, Math.min(1439, Math.round(n)))
  return `${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`
}
function parseHHMM(s: unknown): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(s ?? '').trim())
  if (!m) return null
  const h = Number(m[1])
  const mi = Number(m[2])
  if (h > 23 || mi > 59) return null
  return h * 60 + mi
}

const rangeStartText = computed(() => fmtMin((props.modelValue as [number, number] | undefined)?.[0]))
const rangeEndText = computed(() => fmtMin((props.modelValue as [number, number] | undefined)?.[1]))
const rangeValid = computed(
  () => parseHHMM(rangeStartText.value) !== null && parseHHMM(rangeEndText.value) !== null,
)

function onChoice(v: string) {
  emit('update:modelValue', v)
}
function onThreshold(v: number | null) {
  emit('update:modelValue', v ?? min.value)
}
function onText(v: string) {
  emit('update:modelValue', v)
}
function onRangeStart(v: string) {
  const s = parseHHMM(v)
  const end = (props.modelValue as [number, number] | undefined)?.[1] ?? max.value
  emit('update:modelValue', [s ?? 0, end])
}
function onRangeEnd(v: string) {
  const e = parseHHMM(v)
  const start = (props.modelValue as [number, number] | undefined)?.[0] ?? min.value
  emit('update:modelValue', [start, e ?? max.value])
}

defineExpose({ rangeValid })
</script>

<template>
  <div>
    <n-text v-if="control.prompt" depth="3" style="display: block; margin-bottom: 6px">
      {{ control.prompt }}
    </n-text>

    <n-select
      v-if="widget === 'select'"
      :value="(modelValue as string) ?? null"
      :options="options"
      placeholder="请选择"
      @update:value="onChoice"
    />

    <div v-else-if="widget === 'slider'">
      <n-slider
        :value="(modelValue as number) ?? min"
        :min="min"
        :max="max"
        :step="1"
        :marks="{ [min]: String(min), [max]: String(max) }"
        @update:value="onThreshold"
      />
      <n-text depth="3">当前：{{ (modelValue as number) ?? min }}{{ unit ?? '' }}</n-text>
    </div>

    <n-space v-else-if="widget === 'time_range'" align="center" :wrap="false">
      <n-input
        :value="rangeStartText"
        placeholder="开始 HH:MM"
        style="width: 110px"
        @update:value="onRangeStart"
      />
      <span>→</span>
      <n-input
        :value="rangeEndText"
        placeholder="结束 HH:MM"
        style="width: 110px"
        @update:value="onRangeEnd"
      />
      <n-text :type="rangeValid ? 'default' : 'warning'" depth="3">
        {{ rangeValid ? '（当日时间窗，按分钟提交）' : '请填 HH:MM' }}
      </n-text>
    </n-space>

    <n-input
      v-else-if="widget === 'entity_picker'"
      :value="(modelValue as string) ?? ''"
      :placeholder="control.entity_domain ? `实体 ID（域：${control.entity_domain}）` : '实体 ID'"
      @update:value="onText"
    />

    <n-input
      v-else
      :value="(modelValue as string) ?? ''"
      type="textarea"
      placeholder="输入你的回答"
      @update:value="onText"
    />
  </div>
</template>
