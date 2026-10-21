<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NSpin, NTag, NThing, useMessage,
} from 'naive-ui'
import { facade } from '@/api'

const message = useMessage()

// 当前版本
const versionInfo = ref<Record<string, unknown> | null>(null)
const loadingVersion = ref(false)

// 更新检查
const updateInfo = ref<Record<string, unknown> | null>(null)
const checkingUpdate = ref(false)

// 执行更新
const updating = ref(false)

// 更新状态
const updateStatus = ref<Record<string, unknown> | null>(null)
const pollingStatus = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const error = ref('')

async function loadVersion() {
  loadingVersion.value = true
  error.value = ''
  try {
    const res = await facade.systemVersion()
    versionInfo.value = res.data as Record<string, unknown>
  } catch (e) {
    error.value = `读取版本失败：${String(e)}`
  } finally {
    loadingVersion.value = false
  }
}

async function checkUpdate() {
  checkingUpdate.value = true
  error.value = ''
  try {
    const res = await facade.systemUpdateCheck()
    updateInfo.value = res.data as Record<string, unknown>
  } catch (e) {
    error.value = `检查更新失败：${String(e)}`
  } finally {
    checkingUpdate.value = false
  }
}

async function doUpdate() {
  updating.value = true
  error.value = ''
  try {
    const res = await facade.systemUpdate()
    const data = res.data as Record<string, unknown>
    if (data.ok) {
      message.success(data.message as string || '更新请求已提交')
      // 开始轮询状态
      startPolling()
    } else {
      message.error(data.error as string || '更新请求被拒绝')
    }
  } catch (e) {
    error.value = `提交更新失败：${String(e)}`
  } finally {
    updating.value = false
  }
}

async function loadStatus() {
  try {
    const res = await facade.systemUpdateStatus()
    updateStatus.value = res.data as Record<string, unknown>
    // 如果更新完成且有结果，停止轮询
    const result = (updateStatus.value.result as Record<string, unknown>) || null
    if (!updateStatus.value.pending && result) {
      stopPolling()
      if (result.ok) {
        message.success('更新完成！容器即将重启')
      } else {
        message.error(`更新失败：${result.error || '未知错误'}`)
      }
    }
  } catch (e) {
    // 静默失败，可能是容器重启中
  }
}

function startPolling() {
  stopPolling()
  pollingStatus.value = true
  loadStatus()
  pollTimer = setInterval(loadStatus, 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  pollingStatus.value = false
}

onMounted(() => {
  loadVersion()
  checkUpdate()
  loadStatus()
})
</script>

<template>
  <div>
    <div class="page-head">
      <h1>系统升级</h1>
      <p class="sub">从 GitHub 拉取最新代码并自动部署（宿主侧 sidecar 模式，容器内不写源码）</p>
    </div>

    <n-alert v-if="error" type="error" title="错误" style="margin-bottom: 16px">{{ error }}</n-alert>

    <!-- 当前版本 -->
    <n-card title="当前版本" :bordered="false" class="block">
      <n-spin :show="loadingVersion">
        <div v-if="versionInfo" style="display: flex; gap: 24px; flex-wrap: wrap">
          <n-thing>
            <template #header>版本号</template>
            <n-tag type="info" size="large">{{ versionInfo.version }}</n-tag>
          </n-thing>
          <n-thing>
            <template #header>API 版本</template>
            <n-tag size="large">{{ versionInfo.api_version }}</n-tag>
          </n-thing>
          <n-thing>
            <template #header>契约版本</template>
            <n-tag size="large">{{ versionInfo.contract_version }}</n-tag>
          </n-thing>
          <n-thing v-if="versionInfo.commit">
            <template #header>Commit</template>
            <code style="font-size: 13px">{{ (versionInfo.commit as string).slice(0, 12) }}</code>
          </n-thing>
          <n-thing v-if="versionInfo.branch">
            <template #header>分支</template>
            <n-tag size="large">{{ versionInfo.branch }}</n-tag>
          </n-thing>
        </div>
        <div v-else style="color: #909399">读取中...</div>
      </n-spin>
    </n-card>

    <!-- 检查更新 -->
    <n-card title="检查更新" :bordered="false" class="block">
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px">
        <n-button type="primary" :loading="checkingUpdate" @click="checkUpdate">
          检查更新
        </n-button>
        <span v-if="updateInfo?.checked_at" style="color: #909399; font-size: 13px">
          上次检查：{{ updateInfo.checked_at }}
        </span>
      </div>

      <div v-if="updateInfo">
        <n-alert
          v-if="updateInfo.has_update"
          type="warning"
          title="发现新版本"
          style="margin-bottom: 12px"
        >
          <div style="margin-bottom: 8px">
            当前：<code>{{ (updateInfo.local_commit as string)?.slice(0, 12) || 'unknown' }}</code>
            → 最新：<code>{{ (updateInfo.latest_commit as string)?.slice(0, 12) || 'unknown' }}</code>
          </div>
          <div v-if="(updateInfo.changelog as string[])?.length" style="margin-top: 8px">
            <div style="font-weight: 600; margin-bottom: 4px">变更记录：</div>
            <ul style="margin: 0; padding-left: 20px">
              <li v-for="(item, idx) in updateInfo.changelog" :key="idx" style="font-size: 13px; margin-bottom: 2px">
                {{ item }}
              </li>
            </ul>
          </div>
        </n-alert>
        <n-alert v-else type="success" title="已是最新版本">
          {{ updateInfo.message || '当前版本已是最新' }}
        </n-alert>
      </div>
    </n-card>

    <!-- 执行更新 -->
    <n-card title="执行更新" :bordered="false" class="block">
      <n-alert type="info" title="安全确认" style="margin-bottom: 16px">
        更新操作会从 GitHub 拉取最新代码、重新构建前后端镜像并重启容器。
        宿主脚本带构建失败自动回滚；防误触由写入权限鉴权 + 进行中防重兜底。
      </n-alert>

      <div style="display: flex; align-items: center; gap: 12px">
        <n-button
          type="error"
          :loading="updating"
          :disabled="!versionInfo || pollingStatus"
          @click="doUpdate"
        >
          立即更新
        </n-button>
      </div>
    </n-card>

    <!-- 更新进度 -->
    <n-card v-if="updateStatus" title="更新进度" :bordered="false" class="block">
      <n-spin :show="pollingStatus">
        <div v-if="updateStatus.pending" style="color: #e6a23c">
          <n-tag type="warning">更新进行中</n-tag>
          <span style="margin-left: 12px">宿主脚本正在执行 git pull + 构建 + 部署，请稍候...</span>
        </div>
        <div v-else-if="updateStatus.result" class="result-block">
          <n-alert
            :type="(updateStatus.result as Record<string, unknown>).ok ? 'success' : 'error'"
            :title="(updateStatus.result as Record<string, unknown>).ok ? '更新完成' : '更新失败'"
          >
            <div v-if="(updateStatus.result as Record<string, unknown>).error">
              错误：{{ (updateStatus.result as Record<string, unknown>).error }}
            </div>
            <div v-else style="font-size: 13px">
              <div>从：<code>{{ ((updateStatus.result as Record<string, unknown>).from as string)?.slice(0, 12) }}</code></div>
              <div>到：<code>{{ ((updateStatus.result as Record<string, unknown>).to as string)?.slice(0, 12) }}</code></div>
              <div>完成时间：{{ (updateStatus.result as Record<string, unknown>).finished_at }}</div>
            </div>
          </n-alert>
        </div>
        <div v-else style="color: #909399">暂无进行中的更新</div>
      </n-spin>
    </n-card>
  </div>
</template>

<style scoped>
.block {
  margin-bottom: 16px;
}
.result-block {
  margin-top: 8px;
}
</style>
