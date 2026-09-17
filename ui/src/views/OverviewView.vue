<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  NAlert,
  NCard,
  NGi,
  NGrid,
  NIcon,
  NSpace,
  NSpin,
  NStatistic,
  NTag,
} from 'naive-ui'
import { facade } from '@/api'
import type { GraphListResponse, HealthResponse } from '@/types/api'

const health = ref<HealthResponse | null>(null)
const graphs = ref<GraphListResponse | null>(null)
const loading = ref(true)
const error = ref('')

const shortcuts = [
  { to: '/automations', title: '自动化列表', desc: '浏览全部归档、版本与置信度', tag: '查看' },
  { to: '/simulation', title: '仿真回放', desc: '输入 IR / Seed / 事件，回放执行轨迹', tag: '运行' },
  { to: '/confidence', title: '置信度面板', desc: 'auto / shadow / ask 三档阈值与人工干预', tag: '评估' },
  { to: '/versions', title: '版本与 Diff', desc: '对比归档历史版本的结构化变更', tag: '对比' },
  { to: '/spec-editor', title: 'AF-Spec 工作台', desc: 'AF-Spec 文本编译为 IR 并扫描诊断', tag: '编译' },
  { to: '/faults', title: '故障注入图鉴', desc: '五类故障注入与四类失败模式映射', tag: '查阅' },
]

const archiveCount = computed(() => graphs.value?.items.length ?? 0)

onMounted(async () => {
  try {
    const [h, g] = await Promise.all([facade.health(), facade.graphs()])
    health.value = h.data
    graphs.value = g.data
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>概览</h1>
      <p class="sub">自动化的「编译 / 审批 / 审计台」</p>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 16px">
      {{ error }}
    </n-alert>

    <n-spin :show="loading">
      <n-grid :x-gap="16" :y-gap="16" cols="1 s:2 m:4" responsive="screen" item-responsive>
        <n-gi>
          <n-card :bordered="false" class="stat-card">
            <n-statistic label="运行状态">
              <n-tag :type="health?.ok ? 'success' : 'error'" round size="large" :bordered="false">
                {{ health?.ok ? '● 正常运行' : '● 异常' }}
              </n-tag>
            </n-statistic>
          </n-card>
        </n-gi>
        <n-gi>
          <n-card :bordered="false" class="stat-card">
            <n-statistic label="服务版本" :value="health?.version ?? '—'" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card :bordered="false" class="stat-card">
            <n-statistic label="里程碑" :value="health?.milestones.length ?? 0" />
          </n-card>
        </n-gi>
        <n-gi>
          <n-card :bordered="false" class="stat-card">
            <n-statistic label="归档用例" :value="archiveCount" />
          </n-card>
        </n-gi>
      </n-grid>

      <n-card title="里程碑" :bordered="false" class="block-card">
        <n-space>
          <n-tag
            v-for="m in health?.milestones ?? []"
            :key="m"
            type="success"
            round
            :bordered="false"
          >
            {{ m }}
          </n-tag>
        </n-space>
      </n-card>

      <div class="section-title">快捷入口</div>
      <n-grid :x-gap="16" :y-gap="16" cols="1 s:2 m:3" responsive="screen" item-responsive>
        <n-gi v-for="s in shortcuts" :key="s.to">
          <router-link :to="s.to" class="shortcut-link">
            <n-card :bordered="false" class="shortcut-card" hoverable>
              <div class="shortcut-top">
                <div class="shortcut-title">{{ s.title }}</div>
                <n-tag size="small" :bordered="false" type="info">{{ s.tag }}</n-tag>
              </div>
              <div class="shortcut-desc">{{ s.desc }}</div>
            </n-card>
          </router-link>
        </n-gi>
      </n-grid>
    </n-spin>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 20px;
}
.page-head h1 {
  margin: 0 0 4px;
  font-size: 26px;
}
.page-head .sub {
  margin: 0;
  color: #8b93a7;
  font-size: 14px;
}
.stat-card {
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.block-card {
  margin: 16px 0 24px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.section-title {
  font-size: 15px;
  font-weight: 700;
  color: #4b5563;
  margin: 0 0 12px;
}
.shortcut-link {
  text-decoration: none;
  display: block;
}
.shortcut-card {
  height: 100%;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.shortcut-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(79, 70, 229, 0.14);
}
.shortcut-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.shortcut-title {
  font-size: 15px;
  font-weight: 700;
  color: #1f2937;
}
.shortcut-desc {
  font-size: 13px;
  color: #8b93a7;
  line-height: 1.6;
}
</style>
