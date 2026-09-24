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

/** 同时至多一个生效中的短期码 */
export function activeShortCode (codes: AuthCode[], nowMs: number): AuthCode | null {
  for (const c of codes) {
    if (c.type === 'short' && shortRemainMs(c, nowMs) > 0) return c
  }
  return null
}

export interface CodeKind { label: string; hint: string; tagType: 'info' | 'warning' }

export function codeKindMeta (type: string): CodeKind {
  if (type === 'short') return { label: '短期', hint: '限时有效，过期即失效', tagType: 'warning' }
  return { label: '长期', hint: '长期有效，可随时作废', tagType: 'info' }
}