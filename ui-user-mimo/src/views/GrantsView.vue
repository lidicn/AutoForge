<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NEmpty, NPopconfirm, NTag, useMessage } from 'naive-ui'
import { api } from '../api/index.ts'
import { errorMessage, formatDateTime } from '../logic/format.ts'
import {
  GRANT_STATE_LABEL,
  RECEIPTS_PAGE_LIMIT,
  grantExpiresText,
  grantRemainDays,
  grantRiskLabel,
  grantScopeText,
  grantState,
  receiptTypeMeta,
} from '../logic/grants.ts'
import { useMainStore } from '../stores/main.ts'
import type { Grant, GrantListResponse, GrantReceiptsResponse } from '../types/api.ts'

const store = useMainStore()
const message = useMessage()

const grants = ref<GrantListResponse | null>(null)
const grantsError = ref('')
const grantsBusy = ref(false)
const grantsRefreshedAt = ref('')

const receipts = ref<GrantReceiptsResponse | null>(null)
const receiptsError = ref('')
const receiptsBusy = ref(false)

const revoking = ref<string | null>(null)

function st (g: Grant) { return grantState(g, store.now) }

async function loadGrants () {
  grantsBusy.value = true
  try {
    grants.value = await api.listGrants()
    grantsRefreshedAt.value = new Date().toLocaleTimeString()
    grantsError.value = ''
  } catch (e) {
    // 读失败保留上次成功读数（若有），⛔ 把失败清成「没有授权」
    grantsError.value = String(errorMessage(e, '授权列表读取失败'))
  } finally {
    grantsBusy.value = false
  }
}

async function loadReceipts () {
  receiptsBusy.value = true
  try {
    receipts.value = await api.listGrantReceipts(RECEIPTS_PAGE_LIMIT)
    receiptsError.value = ''
  } catch (e) {
    // 读失败≠没有留痕：这一行不能把故障显示成「暂无记录」
    receiptsError.value = String(errorMessage(e, '留痕读取失败'))
  } finally {
    receiptsBusy.value = false
  }
}

async function revoke (g: Grant) {
  revoking.value = g.grant_id
  try {
    await api.revokeGrant(g.grant_id, '')
    message.success('已撤销，立即生效')
    await Promise.all([loadGrants(), loadReceipts()])
  } catch (e) {
    message.error(errorMessage(e, '撤销失败'))
  } finally {
    revoking.value = null
  }
}

onMounted(() => {
  void loadGrants()
  void loadReceipts()
})
</script>

<template>
  <div class="grants">
    <header class="page-head">
      <h2 class="sec-title">执行授权 <span class="num count">{{ grants?.count ?? '…' }}</span></h2>
      <n-button size="small" secondary :loading="grantsBusy" @click="loadGrants">刷新</n-button>
    </header>
    <p class="sub">
      授权决定 agent 能对哪些设备做什么；这里只管<b>管理／撤销／留痕</b>。撤销<b>立即生效</b>，
      之后任何新执行请求都会拒绝（签发在⛔本页之外）。
    </p>

    <n-alert v-if="grantsError" type="error" title="授权列表读取失败" class="alert">
      {{ grantsError }}
      <div v-if="grantsRefreshedAt" class="alert-sub">页面保留上次成功读数（{{ grantsRefreshedAt }}）</div>
    </n-alert>
    <p v-if="grants?.hidden_count" class="hidden-note">另有 {{ grants.hidden_count }} 条不在你名下</p>

    <ul v-if="grants?.items?.length" class="list">
      <li v-for="g in grants.items" :key="g.grant_id" class="card g-row">
        <div class="g-main">
          <div class="g-top">
            <code class="gid">{{ g.grant_id }}</code>
            <n-tag
              size="tiny"
              :bordered="false"
              :type="st(g) === 'active' ? 'success' : st(g) === 'revoked' ? 'warning' : 'default'"
            >{{ GRANT_STATE_LABEL[st(g)] }}</n-tag>
            <n-tag size="tiny" :bordered="false" type="info">{{ g.agent }}</n-tag>
            <n-tag size="tiny" :bordered="false">{{ grantRiskLabel(g.action_risk) }}</n-tag>
          </div>
          <div class="meta">{{ grantScopeText(g) }}</div>
          <div class="meta">
            签发 {{ formatDateTime(g.issued_at) }} · {{ g.issued_by || '—' }}
            <template v-if="st(g) === 'active'">
              · 剩 <span class="hl">{{ grantRemainDays(g, store.now) }}</span> 天（{{ grantExpiresText(g) }} 到期）
            </template>
            <template v-else-if="st(g) === 'expired'">· 已于 {{ grantExpiresText(g) }} 到期</template>
            <template v-else>· 已撤销{{ g.revoke.reason ? `（${g.revoke.reason}）` : '' }}：{{ formatDateTime(g.revoke.at) }}</template>
            · 已用 {{ g.uses }} 次
          </div>
        </div>
        <div class="g-actions">
          <n-popconfirm
            v-if="st(g) === 'active'"
            :positive-text="`撤销 ${g.grant_id}`"
            negative-text="取消"
            @positive-click="revoke(g)"
          >
            <template #trigger>
              <n-button size="small" secondary type="error" :loading="revoking === g.grant_id">撤销</n-button>
            </template>
            撤销立即生效：此后任何新执行请求都会拒绝，确认撤销 {{ g.grant_id }}？
          </n-popconfirm>
        </div>
      </li>
    </ul>
    <n-empty v-else-if="!grantsError && grants" description="没有执行授权" size="small" />

    <header class="row-head">
      <h2 class="sec-title">最近留痕 <span class="num count">{{ receipts?.count ?? '…' }}</span></h2>
      <n-button size="small" secondary :loading="receiptsBusy" @click="loadReceipts">刷新</n-button>
    </header>
    <n-alert v-if="receiptsError" type="error" title="留痕读取失败" class="alert">
      {{ receiptsError }}
      <div class="alert-sub">⛔ 这不等于「没有留痕」：读取失败与空记录是两件事</div>
    </n-alert>
    <n-alert v-if="receipts?.read_error" type="warning" :bordered="false" class="alert">
      服务端读不到台账（{{ receipts.read_error }}）——这是读数问题，不是「没有留痕」
    </n-alert>
    <p v-if="receipts?.hidden_count" class="hidden-note">另有 {{ receipts.hidden_count }} 条不在你名下</p>

    <ul v-if="receipts?.items?.length" class="list">
      <li v-for="(r, i) in receipts.items" :key="`${r.at}-${i}`" class="card r-row">
        <n-tag size="tiny" :bordered="false" :type="receiptTypeMeta(r.type).tone">
          {{ receiptTypeMeta(r.type).label }}
        </n-tag>
        <span class="num time">{{ formatDateTime(r.at) }}</span>
        <span class="agent">{{ r.agent }}</span>
        <span class="sum">{{ r.summary || '（无摘要）' }}</span>
        <span v-if="r.what?.length" class="what num">{{ r.what.join('、') }}</span>
      </li>
    </ul>
    <n-empty v-else-if="!receiptsError && receipts" description="暂无留痕记录" size="small" />
  </div>
</template>

<style scoped>
.page-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin: 4px 0 6px;
}
.row-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin: 22px 0 10px;
}
.sec-title { margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.08em; color: var(--ink-2); }
.count { color: var(--ink-3); font-size: 12px; }
.sub { margin: 0 0 12px; font-size: 12px; color: var(--ink-3); }
.alert { margin-bottom: 10px; }
.alert-sub { margin-top: 4px; font-size: 12px; color: var(--ink-3); }
.hidden-note { margin: 0 0 10px; font-size: 12px; color: var(--amber); }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.g-row { display: flex; align-items: center; gap: 10px; padding: 12px 14px; }
.g-main { flex: 1; min-width: 0; }
.g-top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.gid { font-size: 12px; color: var(--amber); }
.meta { margin-top: 6px; font-size: 12px; color: var(--ink-3); }
.hl { color: var(--amber); font-weight: 600; }
.g-actions { flex-shrink: 0; }
.r-row { display: flex; align-items: center; gap: 8px; padding: 10px 14px; flex-wrap: wrap; }
.time { font-size: 11px; color: var(--ink-3); white-space: nowrap; }
.agent { font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.sum { flex: 1; min-width: 120px; font-size: 12px; color: var(--ink-2); }
.what { font-size: 11px; color: var(--amber); }
</style>