import type { AuthCode } from '../types/api.ts'

export const SHORT_MIN_MINUTES = 5
export const SHORT_MAX_MINUTES = 30
export const SHORT_CODE_LENGTH = 6

export interface Validate { ok: boolean; message: string }

/** 边界取 fail-closed：越界/非整数一律拒绝，不静默 clamp */
export function validateShortMinutes (v: number): Validate {
  if (!Number.isFinite(v)) return { ok: false, message: '时长必须是数字' }
  if (!Number.isInteger(v)) return { ok: false, message: '时长必须是整分钟' }
  if (v < SHORT_MIN_MINUTES) return { ok: false, message: `最短 ${SHORT_MIN_MINUTES} 分钟` }
  if (v > SHORT_MAX_MINUTES) return { ok: false, message: `最长 ${SHORT_MAX_MINUTES} 分钟` }
  return { ok: true, message: '' }
}

export function clampShortMinutes (v: number): number {
  if (!Number.isFinite(v)) return SHORT_MIN_MINUTES
  return Math.min(SHORT_MAX_MINUTES, Math.max(SHORT_MIN_MINUTES, Math.round(v)))
}

export function shortRemainMs (code: AuthCode, nowMs: number): number {
  if (code.type !== 'short' || !code.expires_at) return 0
  const t = Date.parse(code.expires_at)
  if (Number.isNaN(t)) return 0
  return t - nowMs
}

/** 码的展示态（单一来源）：consumed > revoked > 短期到期 > 生效中。
 *  2026-11-04 缺陷：短期码被 consume 后 `expires_at` 还没到 ⇒ 旧判定只看时间 ⇒ 倒计时继续走，
 *  用户以为还能用（实际再调报「已消耗」）。⇒ 判定必须先把 `consumed`/`revoked` 摘出去。 */
export type CodeStatus = 'active' | 'consumed' | 'revoked' | 'expired'

export const CODE_STATUS_LABEL: Record<CodeStatus, string> = {
  active: '生效中',
  consumed: '已消耗',
  revoked: '已作废',
  expired: '已过期',
}

export function codeStatus (c: AuthCode, nowMs: number): CodeStatus {
  if (c.consumed) return 'consumed'
  if (c.revoked) return 'revoked'
  if (c.type === 'short' && shortRemainMs(c, nowMs) <= 0) return 'expired'
  return 'active'
}

/** 同时至多一个**生效中**的短期码（⛔ 已消耗/已作废的不算生效中 ⇒ 倒计时必须停） */
export function activeShortCode (codes: AuthCode[], nowMs: number): AuthCode | null {
  for (const c of codes) {
    if (c.type === 'short' && codeStatus(c, nowMs) === 'active') return c
  }
  return null
}

/** 最近一枚短期码（含已消耗/已过期）：界面对它显示「已消耗/已过期」，⛔ 不许退回成「无码」让用户以为没生成过 */
export function latestShortCode (codes: AuthCode[]): AuthCode | null {
  let best: AuthCode | null = null
  for (const c of codes) {
    if (c.type !== 'short') continue
    if (!best || Date.parse(c.created_at) > Date.parse(best.created_at)) best = c
  }
  return best
}

export interface CodeKind { label: string; hint: string; tagType: 'info' | 'warning' }

export function codeKindMeta (type: string): CodeKind {
  if (type === 'short') return { label: '短期', hint: '限时有效，过期即失效', tagType: 'warning' }
  return { label: '长期', hint: '长期有效，可随时作废', tagType: 'info' }
}