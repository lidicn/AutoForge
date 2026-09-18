<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NAlert, NCard, NDataTable, NSpace, NText, NTag } from 'naive-ui'
import { h } from 'vue'
import { api } from '@/api/client'

const loading = ref(true)
const error = ref('')
const metrics = ref<any>(null)
const experience = ref<any>(null)
const telemetry = ref<any>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [m, e, t] = await Promise.all([
      api.metrics(),
      api.experience(20),
      api.telemetry(30),
    ])
    metrics.value = m.data
    experience.value = e.data
    telemetry.value = t.data
  } catch (err: any) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <NSpace vertical size="large" style="max-width: 1100px">
    <h2>经验闭环面板</h2>
    <NText depth="2">指标聚合 / 实体共现 / 遥测归因</NText>

    <NAlert v-if="error" type="error" :title="error" />

    <!-- 指标 -->
    <NCard title="指标聚合">
      <pre v-if="metrics">{{ JSON.stringify(metrics, null, 2) }}</pre>
      <NText v-else depth="3">无数据</NText>
    </NCard>

    <!-- 实体共现 -->
    <NCard title="实体共现（成功案例）">
      <pre v-if="experience">{{ JSON.stringify(experience, null, 2) }}</pre>
      <NText v-else depth="3">无数据</NText>
    </NCard>

    <!-- 遥测 -->
    <NCard title="遥测归因（近 30 天）">
      <pre v-if="telemetry">{{ JSON.stringify(telemetry, null, 2) }}</pre>
      <NText v-else depth="3">无数据</NText>
    </NCard>
  </NSpace>
</template>
