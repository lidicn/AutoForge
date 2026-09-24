import type { Agent, AuthCode, Automation, AutomationGroup, PendingItem, PairEvent } from '../types/api'

const BASE = import.meta.env.VITE_API_BASE || '/api'
const TOKEN_KEY = 'forgesight_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t: string): void {
  if (t) localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

interface ReqBody {
  [k: string]: unknown
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) || {}),
  }
  const t = getToken()
  if (t) headers['Authorization'] = 'Bearer ' + t
  const res = await fetch(BASE + path, { ...init, headers })
  if (!res.ok) {
    const body = (await res.json().catch(() => ({ error: res.statusText }))) as { error?: string }
    throw new Error(body.error || res.statusText)
  }
  return (await res.json()) as T
}

export const api = {
  getAgents: () => req<{ ok: boolean; agents: Agent[] }>('/user/agents'),
  deleteAgent: (id: string) => req(`/user/agents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  renameAgent: (id: string, name: string) =>
    req(`/user/agents/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ name }) }),

  listAuthCodes: () => req<{ ok: boolean; codes: AuthCode[] }>('/user/auth-codes'),
  createAuthCode: (kind: string, ttl_minutes?: number) =>
    req('/user/auth-code', { method: 'POST', body: JSON.stringify({ kind, ttl_minutes }) }),
  revokeAuthCode: (code: string) => req(`/user/auth-code/${encodeURIComponent(code)}`, { method: 'DELETE' }),

  listAutomations: (group_by = 'agent') =>
    req<{ ok: boolean; group_by: string; groups?: AutomationGroup[]; items?: Automation[]; total: number }>(
      `/automations?group_by=${group_by}`,
    ),
  getAutomation: (name: string) =>
    req<{ ok: boolean; automation: Automation }>(`/automations/${encodeURIComponent(name)}`),
  enableAutomation: (name: string) => req(`/automations/${encodeURIComponent(name)}/enable`, { method: 'POST' }),
  disableAutomation: (name: string) =>
    req(`/automations/${encodeURIComponent(name)}/disable`, { method: 'POST' }),
  archiveAutomation: (name: string) =>
    req(`/automations/${encodeURIComponent(name)}/archive`, { method: 'POST' }),
  unarchiveAutomation: (name: string) =>
    req(`/automations/${encodeURIComponent(name)}/unarchive`, { method: 'POST' }),
  deleteAutomation: (name: string) => req(`/automations/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  listPending: () => req<{ ok: boolean; ops: PendingItem[] }>('/pending/list', { method: 'POST', body: '{}' }),
  approvePending: (op_id: string) =>
    req('/pending/approve', { method: 'POST', body: JSON.stringify({ op_id }) }),
  rejectPending: (op_id: string, reason: string) =>
    req('/pending/reject', { method: 'POST', body: JSON.stringify({ op_id, reason }) }),

  openPairStream: (
    onEvent: (d: PairEvent) => void,
    onError?: () => void,
  ): { close: () => void } => {
    const t = getToken()
    const url = BASE + '/mcp/pair-request' + (t ? `?token=${encodeURIComponent(t)}` : '')
    const es = new EventSource(url)
    es.addEventListener('pair-request', (ev) => {
      try {
        onEvent(JSON.parse((ev as MessageEvent).data) as PairEvent)
      } catch {
        /* 忽略坏帧 */
      }
    })
    es.onerror = () => onError?.()
    return { close: () => es.close() }
  },
}

export type Api = typeof api
// 占位以避免未使用告警被某些严格配置拦截
export const _reqBody = (b: ReqBody) => b
