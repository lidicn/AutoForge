<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NInput, NSpace, NSwitch, NTag, NText, useMessage,
} from 'naive-ui'
import { facade } from '@/api'
import type { LiveStatusResponse, LiveRunResponse } from '@/types/api'

const message = useMessage()

const status = ref<LiveStatusResponse | null>(null)
const error = ref('')

const irText = ref('')
const allowText = ref('')
const confirm = ref(false)
const running = ref(false)
const result = ref<LiveRunResponse | null>(null)
const runError = ref('')

async function loadStatus() {
  error.value = ''
  try {
    const res = await facade.liveStatus()
    status.value = res.data
  } catch (e) {
    error.value = String(e)
  }
}

function parseAllow(): string[] {
  return allowText.value
    .split(/[\n,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function run() {
  runError.value = ''
  result.value = null
  if (!confirm.value) {
    message.warning('必须显式勾选二次确认')
    return
  }
  let ir: unknown
  try {
    ir = irText.value.trim() ? JSON.parse(irText.value) : {}
  } catch (e) {
    message.error(`IR 不是合法 JSON：${String(e)}`)
    return
  }
  const allow = parseAllow()
  if (allow.length === 0) {
    message.warning('必须提供可写白名单（live_allow），不允许全量下发')
    return
  }
  running.value = true
  try {
    const res = await facade.liveRun(ir, allow, true)
    result.value = res.data
    message.success('真机下发完成')
  } catch (e) {
    runError.value = String(e)
    message.error('下发失败（见下方详情）')
  } finally {
    running.value = false
  }
}

onMounted(loadStatus)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>真机下发</h1>
      <p class="sub">三重闸：服务端启用 + HA 令牌 + 显式二次确认 + 非空白名单（Round 2-B）</p>
    </div>

    <n-alert v-if="error" type="error" title="状态获取失败" style="margin-bottom: 16px">{{ error }}</n-alert>

    <n-card title="可用性" :bordered="false" class="block">
      <template v-if="status">
        <n-space align="center" :size="12">
          <n-tag :type="status.enabled ? 'success' : 'error'" round bordered="false">
            {{ status.enabled ? '已启用' : '未启用' }}
          </n-tag>
          <n-text depth="3">HA：{{ status.ha_url || '（未配置）' }}</n-text>
          <n-text depth="3">白名单必填：{{ status.allowlist_required ? '是' : '否' }}</n-text>
          <n-text depth="3">二次确认必填：{{ status.confirm_required ? '是' : '否' }}</n-text>
        </n-space>
        <n-alert
          v-if="status.reasons.length"
          type="warning"
          title="禁用原因"
          style="margin-top: 12px"
        >
          <ul style="margin: 0; padding-left: 18px">
            <li v-for="(r, i) in status.reasons" :key="i">{{ r }}</li>
          </ul>
        </n-alert>
      </template>
      <n-button text size="small" @click="loadStatus">刷新状态</n-button>
    </n-card>

    <n-card title="下发配置" :bordered="false" class="block">
      <n-space vertical :size="14">
        <div>
          <div class="label">IR（JSON）</div>
          <n-input
            v-model:value="irText"
            type="textarea"
            :autosize="{ minRows: 8, maxRows: 20 }"
            placeholder='粘贴要下发的 IR JSON，例如 {"ir_version":"1.0","id":"x","name":"x","version":1,"nodes":[],"edges":[]}'
            style="font-family: monospace"
          />
        </div>
        <div>
          <div class="label">可写白名单（live_allow，逗号 / 换行分隔的 entity_id）</div>
          <n-input
            v-model:value="allowText"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            placeholder="light.study_main&#10;switch.office_ac"
          />
        </div>
        <div style="display: flex; align-items: center; gap: 10px">
          <n-switch v-model:value="confirm" />
          <n-text :type="confirm ? 'success' : 'error'">我已确认将真实改变设备状态（confirm=true）</n-text>
        </div>
        <div>
          <n-button type="error" :loading="running" :disabled="!confirm" @click="run">下发真机</n-button>
        </div>
      </n-space>
    </n-card>

    <n-alert v-if="runError" type="error" title="下发失败" style="margin-bottom: 16px">
      {{ runError }}
    </n-alert>

    <n-card v-if="result" title="下发结果" :bordered="false" class="block">
      <n-space vertical :size="10">
        <div>模式：<n-tag size="small" type="error" bordered="false">{{ result.mode }}</n-tag> · HA：{{ result.ha_url }}</div>
        <div>写目标：<n-tag v-for="w in result.writes" :key="w" size="small" type="warning" bordered="false" style="margin-right:6px">{{ w }}</n-tag></div>
        <div>允许列表：<n-tag v-for="a in result.allowlist" :key="a" size="small" bordered="false" style="margin-right:6px">{{ a }}</n-tag></div>
        <div v-if="result.nl" class="nl">{{ result.nl }}</div>
        <div v-if="result.asks && result.asks.length" class="asks">有待应答 ask：{{ result.asks.length }} 条</div>
      </n-space>
    </n-card>
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
.block {
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.nl {
  white-space: pre-wrap;
  background: #f8fafc;
  border: 1px solid #eef0f4;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 13px;
}
.asks {
  color: #b45309;
  font-size: 13px;
}
</style>
