/**
 * 执行授权（grant）／任务单（order）的纯展示逻辑（可被 node:test 直接单测）。
 *
 * 纪律：**判定只在后端**。本模块只做「把后端字段翻成人话」——⛔ 不判范围、不判额度、不猜状态。
 * 状态三态（活／过期／撤销）由后端字段派生：`revoke.revoked` 优先于到期时间（撤销是终态）。
 */
import type { Grant, GrantReceipt, Order, OrderReceipt, OrderStep } from '../types/api.ts'
import { formatDateTime } from './format.ts'

export type GrantState = 'active' | 'expired' | 'revoked'

/** 撤销优先、其次到期（epoch 秒）；⛔ 不把缺失 `expires_at` 当永久有效。 */
export function grantState (g: Grant, nowMs: number): GrantState {
  if (g.revoke && g.revoke.revoked) return 'revoked'
  const exp = Number(g.expires_at)
  if (!Number.isFinite(exp) || exp <= 0) return 'expired'
  return exp * 1000 <= nowMs ? 'expired' : 'active'
}

export const GRANT_STATE_LABEL: Record<GrantState, string> = {
  active: '生效中',
  expired: '已过期',
  revoked: '已撤销',
}

/** 剩余整天数（向上取整：还剩 30 分钟也显示「1 天」），已过期归零 */
export function grantRemainDays (g: Grant, nowMs: number): number {
  const exp = Number(g.expires_at)
  if (!Number.isFinite(exp) || exp <= 0) return 0
  const ms = exp * 1000 - nowMs
  return ms <= 0 ? 0 : Math.ceil(ms / 86400000)
}

/** 到期时刻（epoch 秒 → `MM-DD HH:mm`）；读不出就给原文，⛔ 不编一个时刻。 */
export function grantExpiresText (g: Grant): string {
  const exp = Number(g.expires_at)
  if (!Number.isFinite(exp) || exp <= 0) return '未读到有效期'
  return formatDateTime(new Date(exp * 1000).toISOString())
}

/** 范围人话：区域 ＋ 白名单条数 ＋ 排除条数。`all`＝全屋（后端语义）。 */
export function grantScopeText (g: Grant): string {
  const rooms = Array.isArray(g.rooms) ? g.rooms : []
  const inc = Array.isArray(g.device_scope?.include) ? g.device_scope.include.length : 0
  const exc = Array.isArray(g.device_scope?.exclude) ? g.device_scope.exclude.length : 0
  const parts: string[] = []
  if (rooms.length) {
    parts.push('区域 ' + rooms.map((r) => (String(r) === 'all' ? '全屋' : String(r))).join('、'))
  }
  parts.push(`白名单 ${inc} 项`)
  if (exc) parts.push(`排除 ${exc} 项`)
  return parts.join(' · ')
}

export const GRANT_RISK_LABEL: Record<string, string> = {
  read: '只读',
  low: '低风险',
  l2: 'L2 需确认',
  high: '高风险',
}

/** 风险原样回落：后端若给了新枚举，照原文显示而不是显示「未知」 */
export function grantRiskLabel (risk: string): string {
  const raw = String(risk || '').trim()
  if (!raw) return '—'
  return GRANT_RISK_LABEL[raw.toLowerCase()] || raw
}

// ---------------- 任务单 ----------------

export const ORDER_STATUS_LABEL: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
  expired: '已过期',
}

export function orderStatusLabel (status: string): string {
  const raw = String(status || '').trim()
  return ORDER_STATUS_LABEL[raw] || raw || '—'
}

export function orderStatusTone (status: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  switch (String(status || '')) {
    case 'running': return 'info'
    case 'done': return 'success'
    case 'failed': return 'error'
    case 'cancelled': return 'warning'
    case 'expired': return 'default'
    default: return 'default'
  }
}

/** 单步人话：`adapter.action`，params 里的 entity_id 顺带带出（其它参数不进摘要，避免刷屏）。 */
export function orderStepText (step: OrderStep): string {
  const adapter = String(step?.adapter || 'ha')
  const action = String(step?.action || '?')
  const ent = step?.params && typeof step.params.entity_id === 'string' ? step.params.entity_id : ''
  return ent ? `${adapter}.${action} · ${ent}` : `${adapter}.${action}`
}

export function orderStepsBrief (o: Order): string {
  const steps = Array.isArray(o.steps) ? o.steps : []
  if (!steps.length) return '（未记录步骤）'
  const first = orderStepText(steps[0])
  return steps.length === 1 ? first : `${steps.length} 步 · 首步 ${first}`
}

/** 到点时刻（epoch 秒 → `MM-DD HH:mm`）；`at` 型与 `delay` 型同一套显示 */
export function orderDueText (o: Order): string {
  const due = Number(o.due_at)
  if (!Number.isFinite(due) || due <= 0) return '未读到到点时刻'
  return formatDateTime(new Date(due * 1000).toISOString())
}

/** 输出通道人话：只读语料，⛔ 不承诺推送（`mimo` 通道是「可读」不是「已推送」） */
export function orderOutputText (o: Order): string {
  const ch = Array.isArray(o.output?.channels) ? o.output.channels : []
  return ch.length ? ch.join(' / ') : '（默认 tts）'
}

// ---------------- 留痕 ----------------

export interface ReceiptTypeMeta {
  label: string
  tone: 'default' | 'info' | 'success' | 'warning' | 'error'
}

const RECEIPT_TYPES: Record<string, ReceiptTypeMeta> = {
  GRANT_ISSUED: { label: '签发授权', tone: 'success' },
  GRANT_REVOKED: { label: '撤销授权', tone: 'warning' },
  GRANT_RENEWED: { label: '续期授权', tone: 'info' },
  TASK_ORDER_CREATED: { label: '挂单', tone: 'info' },
  TASK_ORDER_FINISHED: { label: '执行完成', tone: 'success' },
  TASK_ORDER_CANCELLED: { label: '取消', tone: 'warning' },
  TASK_ORDER_EXPIRED: { label: '过期未执行', tone: 'default' },
  LIVE_RUN_EXECUTED: { label: '真机执行', tone: 'info' },
}

/** 未知流水类型原样显示（⛔ 不塞进「其它」把真实类型藏掉） */
export function receiptTypeMeta (type: string): ReceiptTypeMeta {
  const raw = String(type || '').trim()
  return RECEIPT_TYPES[raw] || { label: raw || '—', tone: 'default' }
}

export interface OrderReceiptGroup {
  order_id: string
  items: OrderReceipt[]
}

/** 按 order_id 聚合留痕，**组序＝首次出现序**（后端已是新→旧），组内保持后端顺序。 */
export function groupOrderReceipts (items: OrderReceipt[]): OrderReceiptGroup[] {
  const groups: OrderReceiptGroup[] = []
  const index = new Map<string, OrderReceiptGroup>()
  for (const it of Array.isArray(items) ? items : []) {
    const id = String(it?.order_id || '').trim() || '（未记录单号）'
    let g = index.get(id)
    if (!g) {
      g = { order_id: id, items: [] }
      index.set(id, g)
      groups.push(g)
    }
    g.items.push(it)
  }
  return groups
}

/** 「另有 N 条不在你名下」：0/缺失一律不显示（⛔ owner 面常驻噪音） */
export function hiddenCountText (n?: number): string | null {
  const v = Number(n)
  return Number.isFinite(v) && v > 0 ? `另有 ${v} 条不在你名下` : null
}

/** 留痕条数上限（与后端 LIVE_RECEIPTS_LIMIT 默认同为 20；页面只读展示，不做分页拉全量） */
export const RECEIPTS_PAGE_LIMIT = 20
