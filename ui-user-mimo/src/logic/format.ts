/** 时间 / 倒计时 / 错误文案格式化（纯函数，可被 node:test 直接单测） */

export function pad2 (n: number): string {
  const v = Math.trunc(Math.abs(n))
  return (n < 10 && n >= 0 ? '0' : '') + String(v)
}

/** MM:SS；不足 1s 向上取整（避免"还有时间却显示 00:00"），负数归零，分钟不截断 */
export function formatCountdown (ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  return pad2(Math.floor(total / 60)) + ':' + pad2(total % 60)
}

export function formatDateTime (iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** 相对时间分档：刚刚 / N 分钟前 / N 小时前 / N 天前 / 超 30 天回落绝对时间 */
export function formatAgo (iso: string | null, nowMs: number): string {
  if (!iso) return '从未触发'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '从未触发'
  const delta = Math.max(0, nowMs - t)          // 时钟漂移（未来时间）不显示负数
  const min = Math.floor(delta / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day <= 30) return `${day} 天前`
  return formatDateTime(iso)
}

export function errorMessage (e: unknown, fallback = '操作失败'): string {
  if (e instanceof Error && e.message) return e.message
  if (typeof e === 'string' && e) return e
  return fallback
}