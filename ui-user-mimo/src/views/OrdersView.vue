<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NAlert, NButton, NEmpty, NPopconfirm, NTag, useMessage } from 'naive-ui'
import { api } from '../api/index.ts'
import UiIcon from '../components/UiIcon.vue'
import { errorMessage, formatDateTime } from '../logic/format.ts'
import {
  RECEIPTS_PAGE_LIMIT,
  groupOrderReceipts,
  orderDueText,
  orderOutputText,
  orderStatusLabel,
  orderStatusTone,
  orderStepText,
  orderStepsBrief,
  receiptTypeMeta,
} from '../logic/grants.ts'
import type { Order, OrderListResponse, OrderReceiptsResponse } from '../types/api.ts'

const message = useMessage()

const orders = ref<OrderListResponse | null>(null)
const ordersError = ref('')
const ordersBusy = ref(false)
const ordersRefreshedAt = ref('')

const receipts = ref<OrderReceiptsResponse | null>(null)
const receiptsError = ref('')
const receiptsBusy = ref(false)

const canceling = ref<string | null>(null)
// 展开的活动单（步骤/输出明细），默认收起
const expanded = ref<Set<string>>(new Set())

const groups = ref<ReturnType<typeof groupOrderReceipts>>([])

async function loadOrders () {
  ordersBusy.value = true
  try {
    orders.value = await api.listOrders()
    ordersRefreshedAt.value = new Date().toLocaleTimeString()
    ordersError.value = ''
  } catch (e) {
    // 读失败保留上次成功读数（若有），⛔ 把失败清成「没有任务单」
    ordersError.value = String(errorMessage(e, '任务单列表读取失败'))
  } finally {
    ordersBusy.value = false
  }
}

async function loadReceipts () {
  receiptsBusy.value = true
  try {
    receipts.value = await api.listOrderReceipts(RECEIPTS_PAGE_LIMIT)
    groups.value = groupOrderReceipts(receipts.value.items || [])
    receiptsError.value = ''
  } catch (e) {
    receiptsError.value = String(errorMessage(e, '回执读取失败'))
  } finally {
    receiptsBusy.value = false
  }
}

function toggleExpanded (id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

async function cancel (o: Order) {
  canceling.value = o.order_id
  try {
    await api.cancelOrder(o.order_id, '')
    message.success('任务单已取消（终态即销，历史留在回执里）')
    await Promise.all([loadOrders(), loadReceipts()])
  } catch (e) {
    message.error(errorMessage(e, '取消失败'))
  } finally {
    canceling.value = null
  }
}

onMounted(() => {
  void loadOrders()
  void loadReceipts()
})
</script>

<template>
  <div class="orders">
    <header class="page-head">
      <h2 class="sec-title">任务单 <span class="num count">{{ orders?.count ?? '…' }}</span></h2>
      <n-button size="small" secondary :loading="ordersBusy" @click="loadOrders">刷新</n-button>
    </header>
    <p class="sub">活动池只含待执行／执行中；完成后即销，历史走下方回执（`mimo` 通道＝可读，不是已推送）。</p>

    <n-alert v-if="ordersError" type="error" title="任务单读取失败" class="alert">
      {{ ordersError }}
      <div v-if="ordersRefreshedAt" class="alert-sub">页面保留上次成功读数（{{ ordersRefreshedAt }}）</div>
    </n-alert>
    <p v-if="orders?.hidden_count" class="hidden-note">另有 {{ orders.hidden_count }} 条不在你名下</p>

    <ul v-if="orders?.items?.length" class="list">
      <li v-for="o in orders.items" :key="o.order_id" class="card o-row">
        <div class="o-main">
          <div class="o-top">
            <code class="oid">{{ o.order_id }}</code>
            <n-tag size="tiny" :bordered="false" :type="orderStatusTone(o.status)">{{ orderStatusLabel(o.status) }}</n-tag>
            <n-tag size="tiny" :bordered="false" type="info">{{ o.agent }}</n-tag>
            <span class="num due">↦ {{ orderDueText(o) }}</span>
          </div>
          <div class="meta">{{ orderStepsBrief(o) }}</div>
          <div class="meta">输出 → {{ orderOutputText(o) }}<template v-if="o.output?.text"> · {{ o.output.text }}</template></div>
          <template v-if="expanded.has(o.order_id)">
            <div class="detail">
              <div v-for="(s, i) in o.steps" :key="`${o.order_id}-${i}`" class="step">
                <span class="num step-i">{{ i + 1 }}.</span> {{ orderStepText(s) }}
              </div>
            </div>
          </template>
        </div>
        <div class="o-actions">
          <button class="icon-btn" :title="expanded.has(o.order_id) ? '收起明细' : '展开明细'" @click="toggleExpanded(o.order_id)">
            <ui-icon name="chevron" :size="16" />
          </button>
          <n-popconfirm
            :positive-text="`取消 ${o.order_id}`"
            negative-text="不取消"
            @positive-click="cancel(o)"
          >
            <template #trigger>
              <n-button size="small" secondary type="error" :loading="canceling === o.order_id">取消</n-button>
            </template>
            取消后任务不会执行（终态即销，历史留在回执里）。确认取消 {{ o.order_id }}？
          </n-popconfirm>
        </div>
      </li>
    </ul>
    <n-empty v-else-if="!ordersError && orders" description="当前没有任务单" size="small" />

    <header class="row-head">
      <h2 class="sec-title">逐单回执 <span class="num count">{{ receipts?.count ?? '…' }}</span></h2>
      <n-button size="small" secondary :loading="receiptsBusy" @click="loadReceipts">刷新</n-button>
    </header>
    <n-alert v-if="receiptsError" type="error" title="回执读取失败" class="alert">
      {{ receiptsError }}
      <div class="alert-sub">⛔ 这不等于「没有回执」：读取失败与空记录是两件事</div>
    </n-alert>
    <n-alert v-if="receipts?.read_error" type="warning" :bordered="false" class="alert">
      服务端读不到台账（{{ receipts.read_error }}）——这是读数问题，不是「没有回执」
    </n-alert>
    <p v-if="receipts?.hidden_count" class="hidden-note">另有 {{ receipts.hidden_count }} 条不在你名下</p>

    <div v-if="groups.length" class="groups">
      <section v-for="grp in groups" :key="grp.order_id" class="card g-group">
        <header class="g-head">
          <code class="oid">{{ grp.order_id }}</code>
          <span class="num time">{{ grp.items[0].agent }}</span>
          <span class="g-tail num">{{ grp.items.length }} 条记录</span>
        </header>
        <ul class="tl">
          <li v-for="(r, i) in grp.items" :key="`${r.at}-${i}`" class="tl-row">
            <n-tag size="tiny" :bordered="false" :type="receiptTypeMeta(r.type).tone">
              {{ receiptTypeMeta(r.type).label }}
            </n-tag>
            <span class="num time">{{ formatDateTime(r.at) }}</span>
            <span class="sum">
              {{ receiptTypeMeta(r.type).label === '取消' && r.reason ? `原因：${r.reason}` : '—' }}
            </span>
          </li>
        </ul>
      </section>
    </div>
    <n-empty v-else-if="!receiptsError && receipts" description="暂无回执记录" size="small" />
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
.o-row { display: flex; align-items: center; gap: 10px; padding: 12px 14px; }
.o-main { flex: 1; min-width: 0; }
.o-top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.oid { font-size: 12px; color: var(--amber); }
.due { font-size: 11px; color: var(--ink-3); }
.meta { margin-top: 6px; font-size: 12px; color: var(--ink-3); }
.detail { margin-top: 8px; padding: 8px 10px; border-radius: 8px; background: var(--bg-sunken); }
.step { font-size: 12px; color: var(--ink-2); padding: 2px 0; }
.step-i { color: var(--ink-3); }
.o-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.groups { display: flex; flex-direction: column; gap: 10px; }
.g-group { padding: 12px 14px; }
.g-head { display: flex; align-items: center; gap: 8px; }
.g-tail { margin-left: auto; color: var(--ink-3); font-size: 11px; }
.tl { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.tl-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.time { font-size: 11px; color: var(--ink-3); white-space: nowrap; }
.sum { font-size: 12px; color: var(--ink-2); }
</style>