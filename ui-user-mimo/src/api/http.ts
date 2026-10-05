/**
 * AutoForge 用户端 · 真实 HTTP 客户端
 * ------------------------------------------------------------------
 * 实现与 mock.ts 完全相同的 ApiClient 接口；视图 / store 契约不变。
 * 以「最小改动」为原则：后端只补了登录与配对确认端点，其余路径 / 字段差异在此处翻译。
 * Token 由本层持有（localStorage），随请求带 Authorization；SSE 因 EventSource
 * 不能带自定义头，改用 ?token= 回退（后端已支持）。
 */
import type { Agent, AuthCode, Automation, PairRequest, PendingItem, User } from '../types/api.ts'
import { apiError, type ApiClient, type ApiErrorCode } from './mock.ts'
import { API_BASE } from './env.ts'

const TOKEN_KEY = 'forgesight_token'

// ---------------- token 持久化 ----------------
function loadToken (): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
function saveToken (t: string | null): void {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* ignore */ }
}

let token: string | null = loadToken()

export function getToken (): string | null { return token }
export function setToken (t: string | null): void { token = t; saveToken(t) }
export function clearToken (): void { setToken(null) }

// ---------------- 配对 SSE ----------------
let es: EventSource | null = null
let currentPair: PairRequest | null = null
let pairListener: ((p: PairRequest) => void) | null = null

export function onPairRequest (cb: (p: PairRequest) => void): void { pairListener = cb }

export function openPairStream (): void {
  if (es || typeof EventSource === 'undefined') return
  const url = `${API_BASE}/api/mcp/pair-request${token ? `?token=${encodeURIComponent(token)}` : ''}`
  es = new EventSource(url)
  es.addEventListener('pair-request', (ev) => {
    try {
      const raw = JSON.parse((ev as MessageEvent).data) as {
        agent_name_hint: string; code: string; expires_at: number
      }
      const req: PairRequest = {
        agent_name_hint: raw.agent_name_hint,
        code: raw.code,
        expires_at: toIso(raw.expires_at) ?? new Date().toISOString(),
      }
      currentPair = req
      pairListener?.(req)
    } catch { /* 忽略坏帧 */ }
  })
  es.onerror = () => { /* 浏览器会自动重连 */ }
}

export function closePairStream (): void { es?.close(); es = null }

// 登录态恢复：带 token 时调 /api/auth/me；失效则清 token 返回 null
export async function restoreSession (): Promise<User | null> {
  if (!token) return null
  try {
    const data = await req<{ ok: boolean; user: User }>('/api/auth/me')
    return data.user
  } catch {
    clearToken()
    return null
  }
}

// ---------------- 底层请求 ----------------
function mapCode (status: number, body: any): ApiErrorCode {
  if (status === 401 || status === 403) return 'AUTH_FAILED'
  if (status === 404) return 'NOT_FOUND'
  if (status === 409) return 'INVALID_STATE'
  if (status === 410) return 'PAIR_INVALID'
  if (status === 400 || status === 422) return 'BAD_REQUEST'
  return 'NETWORK_ERROR'
}

async function req<T> (path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  } catch (e) {
    throw apiError('NETWORK_ERROR', '网络错误：无法连接后端')
  }
  if (res.status === 401) {
    clearToken()
    throw apiError('AUTH_FAILED', '登录已失效，请重新登录')
  }
  let body: any = null
  try { body = await res.json() } catch { /* 可能无响应体 */ }
  if (!res.ok) {
    const msg = body?.detail || body?.error || body?.message || `请求失败 (${res.status})`
    throw apiError(mapCode(res.status, body), typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return body as T
}

// ---------------- 字段映射 ----------------
function toIso (v: any): string | null {
  if (v == null) return null
  if (typeof v === 'number') return new Date(v * 1000).toISOString()
  return String(v)
}

function mapStatus (c: any): Automation['status'] {
  if (c.archived) return 'archived'
  if (c.pending_op_id) return 'pending'
  if (c.trial && c.trial.anomaly) return 'anomaly'
  return c.enabled ? 'enabled' : 'disabled'
}

function mapAutomation (c: any): Automation {
  return {
    id: c.id,
    name: c.name,
    agent_id: c.agent || '',
    agent_name: c.agent || '',
    status: mapStatus(c),
    preview_nl: c.preview_nl || '',
    devices: Array.isArray(c.devices) ? c.devices : [],
    last_triggered: toIso(c.last_triggered),
    trigger_7d: c.trigger_7d || 0,
    archived: !!c.archived,
    trial: c.trial
      ? {
          state: c.trial.state,
          since: toIso(c.trial.since) ?? '',
          anomaly: c.trial.anomaly ? String(c.trial.anomaly) : null,
        }
      : null,
  }
}

// ---------------- ApiClient 实现 ----------------
export const httpApi: ApiClient = {
  now () { return Date.now() },

  async login (username, password) {
    const data = await req<{ ok: boolean; user: User; token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setToken(data.token)
    return data.user
  },

  async logout () {
    try { await req('/api/auth/logout', { method: 'POST' }) } catch { /* fail-open */ }
    clearToken()
  },

  async listAgents () {
    const data = await req<{ ok: boolean; agents: Array<{ id: string; name: string; scopes: string[] }> }>('/api/user/agents')
    return (data.agents || []).map((a) => ({
      agent_id: a.id,
      name: a.name,
      connected_at: null,
      last_seen: null,
    }))
  },

  async renameAgent (agentId, name) {
    const data = await req<{ ok: boolean; renamed: number; from: string; to: string }>(
      `/api/user/agents/${encodeURIComponent(agentId)}`,
      { method: 'PATCH', body: JSON.stringify({ name }) },
    )
    return { agent_id: agentId, name: data.to, connected_at: null, last_seen: null }
  },

  async deleteAgent (agentId) {
    await req(`/api/user/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' })
  },

  // 真实流程由 SSE 驱动：agent 经 MCP 发起配对 → 后端推码 → 这里直接返回最新一帧
  async createPairRequest (_agentNameHint: string) {
    if (currentPair) return currentPair
    throw apiError('PAIR_INVALID', '尚未收到配对请求（请让 agent 经 MCP 发起配对）')
  },

  async resolvePair (code) {
    const data = await req<{ ok: boolean; agent: Agent }>(
      `/api/user/pair/${encodeURIComponent(code)}/confirm`,
      { method: 'POST' },
    )
    return data.agent
  },

  async listAutomations () {
    const data = await req<{ ok: boolean; items: any[]; total: number }>('/api/automations?group_by=flat')
    return (data.items || []).map(mapAutomation)
  },

  async setAutomationEnabled (id, enabled) {
    await req(`/api/automations/${encodeURIComponent(id)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' })
    const detail = await req<{ ok: boolean; automation: any }>(`/api/automations/${encodeURIComponent(id)}`)
    return mapAutomation(detail.automation)
  },

  async setAutomationArchived (id, archived) {
    await req(`/api/automations/${encodeURIComponent(id)}/${archived ? 'archive' : 'unarchive'}`, { method: 'POST' })
    const detail = await req<{ ok: boolean; automation: any }>(`/api/automations/${encodeURIComponent(id)}`)
    return mapAutomation(detail.automation)
  },

  async deleteAutomation (id) {
    await req(`/api/automations/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  async listPending () {
    const data = await req<{ ok: boolean; items?: any[]; pendings?: any[] }>('/api/pending/list', {
      method: 'POST',
      body: '{}',
    })
    const raw = data.items ?? data.pendings ?? []
    return raw.map((p) => ({
      op_id: p.op_id,
      summary: p.summary || '',
      agent_name: p.agent || '',
      submitted_at: typeof p.submitted_at === 'string' ? p.submitted_at : (toIso(p.submitted_at) ?? ''),
      blast_radius: typeof p.blast_radius === 'object' && p.blast_radius !== null
        ? JSON.stringify(p.blast_radius)
        : (p.blast_radius || ''),
    })) as PendingItem[]
  },

  async resolvePending (opId, approve) {
    await req(`/api/pending/${approve ? 'approve' : 'reject'}`, {
      method: 'POST',
      body: JSON.stringify(approve ? { op_id: opId } : { op_id: opId, reason: '' }),
    })
  },

  async listAuthCodes () {
    const data = await req<{ ok: boolean; codes: Array<{ code: string; kind: string; created_at: number; expires_at: number | null; revoked?: boolean }> }>('/api/user/auth-codes')
    return (data.codes || [])
      .filter((c) => !c.revoked)
      .map((c) => ({
        code: c.code,
        type: c.kind === 'short' ? 'short' : 'long',
        created_at: toIso(c.created_at) ?? new Date().toISOString(),
        expires_at: c.expires_at ? toIso(c.expires_at) : null,
      }))
  },

  async createLongCode () {
    const data = await req<{ ok: boolean; code: string; kind: string; expires_at: number | null }>(
      '/api/user/auth-code',
      { method: 'POST', body: JSON.stringify({ kind: 'long' }) },
    )
    return {
      code: data.code,
      type: 'long',
      created_at: new Date().toISOString(),
      expires_at: data.expires_at ? toIso(data.expires_at) : null,
    }
  },

  async createShortCode (minutes) {
    const data = await req<{ ok: boolean; code: string; kind: string; expires_at: number | null }>(
      '/api/user/auth-code',
      { method: 'POST', body: JSON.stringify({ kind: 'short', ttl_minutes: minutes }) },
    )
    return {
      code: data.code,
      type: 'short',
      created_at: new Date().toISOString(),
      expires_at: data.expires_at ? toIso(data.expires_at) : null,
    }
  },

  async revokeAuthCode (code) {
    await req(`/api/user/auth-code/${encodeURIComponent(code)}`, { method: 'DELETE' })
  },

}
