export const PAIR_CODE_LENGTH = 6
export const PAIR_TTL_MS = 5 * 60_000

export function isPairCode (value: string): boolean {
  return new RegExp(`^\\d{${PAIR_CODE_LENGTH}}$`).test(value)
}

/** 纯随机源可注入，单测可确定性断言 */
export function randomDigits (n = PAIR_CODE_LENGTH, rng: () => number = Math.random): string {
  let out = ''
  for (let i = 0; i < n; i++) out += String(Math.floor(rng() * 10) % 10)
  return out
}

/** 逐位翻入的错峰延迟表：[0, 90, 180, ...] */
export function flipDelays (count = PAIR_CODE_LENGTH, step = 90): number[] {
  return Array.from({ length: Math.max(0, count) }, (_, i) => i * step)
}

export function countdownMs (expiresAt: string, nowMs: number): number {
  const t = Date.parse(expiresAt)
  if (Number.isNaN(t)) return 0
  return t - nowMs
}

/** 边界语义：expires_at 当刻即算过期 */
export function isExpired (expiresAt: string, nowMs: number): boolean {
  return countdownMs(expiresAt, nowMs) <= 0
}