/** 会话存储：localStorage 不可用（隐私模式/被禁）时降级到内存，全部 fail-open */
export interface KV {
  getItem (key: string): string | null
  setItem (key: string, value: string): void
  removeItem (key: string): void
}

const memoryData = new Map<string, string>()
const memoryKV: KV = {
  getItem: (key) => (memoryData.has(key) ? String(memoryData.get(key)) : null),
  setItem: (key, value) => { memoryData.set(key, value) },
  removeItem: (key) => { memoryData.delete(key) },
}

let backend: KV | null = null

function resolve (): KV {
  if (backend) return backend
  try {
    const ls = (globalThis as { localStorage?: KV }).localStorage
    if (ls) {
      ls.setItem('__af_probe__', '1')
      ls.removeItem('__af_probe__')
      backend = ls
      return backend
    }
  } catch { /* 降级到内存 */ }
  backend = memoryKV
  return backend
}

export function readJSON<T> (key: string, fallback: T): T {
  try {
    const raw = resolve().getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch { return fallback }
}

export function writeJSON (key: string, value: unknown): void {
  try { resolve().setItem(key, JSON.stringify(value)) } catch { /* fail-open */ }
}

export function deleteKey (key: string): void {
  try { resolve().removeItem(key) } catch { /* fail-open */ }
}

/** 测试注入点：传 null 复位到"自动探测 + 内存兜底" */
export function __setBackend (kv: KV | null): void {
  backend = kv
  memoryData.clear()
}