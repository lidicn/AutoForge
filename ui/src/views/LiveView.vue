<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NEmpty, NInput, NSpace, NSwitch, NTag, NText, NPopconfirm, useMessage,
} from 'naive-ui'
import { facade } from '@/api'
import type {
  LiveStatusResponse, LiveRunResponse, UndoRunResponse,
} from '@/types/api'

const message = useMessage()

const status = ref<LiveStatusResponse | null>(null)
const error = ref('')

const irText = ref('')
const allowText = ref('')
// 事件脚本：后端 `_replay_live` 靠它把触发条件喂进 runtime。此前这里恒传 undefined
// ⇒ 回放空转，trigger 型自动化在真机档一次都不会命中（F4 ③ 前端残留）。
const eventsText = ref('')
const confirm = ref(false)
const undo = ref(true)
const running = ref(false)
const result = ref<LiveRunResponse | null>(null)
const runError = ref('')

// F7 撤销：撤销 = 再下发一次，二次确认由 popconfirm 承担（对应后端 confirm=true）
const undoItems = ref<{ deploy_id: string; age_s: number; entities: string[] }[]>([])
const undoWindowS = ref(0)
const undoBusy = ref(false)
const undoResult = ref<UndoRunResponse | null>(null)
const undoError = ref('')

async function loadStatus() {
  error.value = ''
  try {
    const res = await facade.liveStatus()
    status.value = res.data
  } catch (e) {
    error.value = String(e)
  }
}

async function loadUndoList() {
  try {
    const res = await facade.undoAvailable()
    undoItems.value = res.data.items
    undoWindowS.value = res.data.window_s
  } catch {
    // 清单读不到不阻断主流程，但不能假装"没有可撤销项"是设备侧结论
    undoItems.value = []
    undoWindowS.value = 0
  }
}

async function revert(id: string) {
  undoBusy.value = true
  undoError.value = ''
  undoResult.value = null
  try {
    const res = await facade.undoDeploy(id, true)
    undoResult.value = res.data
    if (res.data.ok) {
      message.success(res.data.fully_restored ? '已完整回滚' : '已回滚（存在部分恢复，见下方）')
    } else {
      message.warning(`撤销被拒绝：${res.data.reason}`)
    }
    await loadUndoList()
  } catch (e) {
    undoError.value = String(e)
    message.error('撤销请求失败（真机闸门 / 权限，见后端详情）')
  } finally {
    undoBusy.value = false
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
  let events: unknown[] = []
  if (eventsText.value.trim()) {
    try {
      const parsed = JSON.parse(eventsText.value)
      if (!Array.isArray(parsed)) throw new Error('事件脚本必须是数组')
      events = parsed
    } catch (e) {
      message.error(`事件脚本不是合法 JSON 数组：${String(e)}`)
      return
    }
  }
  running.value = true
  try {
    const res = await facade.liveRun(ir, allow, true, events, undo.value)
    result.value = res.data
    message.success(events.length ? `真机下发完成（回放 ${events.length} 条事件）` : '真机下发完成（未提供事件脚本：trigger 型自动化不会命中）')
    await loadUndoList()
  } catch (e) {
    runError.value = String(e)
    message.error('下发失败（见下方详情）')
  } finally {
    running.value = false
  }
}

onMounted(() => {
  loadStatus()
  loadUndoList()
})
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
        <div>
          <div class="label">事件脚本（可空）——按顺序回放进 runtime，用于让 trigger 型自动化在真机档命中</div>
          <n-input
            v-model:value="eventsText"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 8 }"
            placeholder='[{"entity_id":"binary_sensor.motion","state":"on"},{"advance_s":30}]'
            style="font-family: monospace"
          />
        </div>
        <div style="display: flex; align-items: center; gap: 10px">
          <n-switch v-model:value="confirm" />
          <n-text :type="confirm ? 'success' : 'error'">我已确认将真实改变设备状态（confirm=true）</n-text>
        </div>
        <div style="display: flex; align-items: center; gap: 10px">
          <n-switch v-model:value="undo" />
          <n-text depth="3">记录动作前快照，{{ undoWindowS || 60 }}s 窗口内可撤销本次部署（F7 / 决策 E）</n-text>
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
        <n-alert
          v-if="result.missing_entities && result.missing_entities.length"
          type="warning"
          :title="`真机终态读不到 ${result.missing_entities.length} 个实体（值记为空，未编造）`"
        >
          <code>{{ result.missing_entities.join('、') }}</code>
        </n-alert>
        <div>允许列表：<n-tag v-for="a in result.allowlist" :key="a" size="small" bordered="false" style="margin-right:6px">{{ a }}</n-tag></div>
        <div v-if="result.nl" class="nl">{{ result.nl }}</div>
        <div v-if="result.asks && result.asks.length" class="asks">有待应答 ask：{{ result.asks.length }} 条</div>
        <div v-if="result.undo_deploy_id" class="asks">
          撤销 ID：<code>{{ result.undo_deploy_id }}</code>
          （窗口 {{ result.undo_window_s }}s · 可在下方「可撤销的部署」一键回滚）
        </div>
        <div v-else-if="result.undo_enabled" class="asks">
          已开启快照记录，但本次下发没有落到任何写动作 → 无可撤销快照
        </div>
      </n-space>
    </n-card>

    <n-card title="可撤销的部署（时间窗内）" :bordered="false" class="block">
      <n-alert v-if="undoError" type="error" title="撤销请求失败" style="margin-bottom: 12px">
        {{ undoError }}
      </n-alert>
      <n-space align="center" :size="10" style="margin-bottom: 8px">
        <n-text depth="3">窗口 {{ undoWindowS }}s · {{ undoItems.length }} 条可撤销</n-text>
        <n-button text size="small" @click="loadUndoList">刷新清单</n-button>
      </n-space>
      <n-empty v-if="!undoItems.length" description="窗口内没有可撤销的部署" size="small" />
      <div v-for="item in undoItems" :key="item.deploy_id" class="undo-row">
        <div>
          <code>{{ item.deploy_id }}</code>
          <n-text depth="3" style="margin-left: 8px">{{ item.age_s.toFixed(1) }}s 前</n-text>
          <div class="ents">{{ item.entities.join('、') || '（无实体记录）' }}</div>
        </div>
        <n-popconfirm
          positive-text="确认回滚"
          negative-text="先不"
          @positive-click="revert(item.deploy_id)"
        >
          <template #trigger>
            <n-button size="small" type="warning" :loading="undoBusy" :disabled="undoBusy">
              撤销
            </n-button>
          </template>
          撤销会对真实设备再下发一次恢复动作（含风险域二次确认），确认？
        </n-popconfirm>
      </div>

      <template v-if="undoResult">
        <n-alert
          :type="undoResult.ok ? (undoResult.fully_restored ? 'success' : 'warning') : 'error'"
          :title="undoResult.ok ? (undoResult.fully_restored ? '已完整回滚' : '已回滚（非完整）') : `撤销被拒绝：${undoResult.reason}`"
          style="margin-top: 12px"
        >
          <div v-if="undoResult.message">{{ undoResult.message }}</div>
          <div v-if="undoResult.restored?.length">已恢复：{{ undoResult.restored.join('、') }}</div>
          <div v-if="undoResult.partial?.length" class="warn">
            部分恢复：
            <div v-for="p in undoResult.partial" :key="p.entity_id">
              {{ p.entity_id }} 已下发 {{ p.action }}，快照里 {{ p.not_restored.join('/') }} 读不出、未回放
            </div>
          </div>
          <div v-if="undoResult.skipped?.length" class="warn">
            跳过（读不出恢复目标 / 无恢复映射，未写设备）：{{ undoResult.skipped.join('、') }}
          </div>
          <div v-if="undoResult.failed?.length" class="warn">
            恢复失败：{{ undoResult.failed.join('、') }}
          </div>
          <div v-if="undoResult.risk_entities?.length">
            风险域：{{ undoResult.risk_entities.join('、') }}
          </div>
        </n-alert>
      </template>
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
.undo-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 0;
  border-top: 1px solid #eef0f4;
}
.undo-row:first-of-type {
  border-top: none;
}
.ents {
  color: #8b93a7;
  font-size: 12px;
  margin-top: 4px;
  word-break: break-all;
}
.warn {
  color: #b45309;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 6px;
}
</style>
