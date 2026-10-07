import type {
  Agent,
  AuthCode,
  Automation,
  AutomationGroup,
  PairAccepting,
  PairAcceptingState,
  PairEvent,
  PendingItem,
} from '../types/api'
import type { Api } from './client'

const delay = (ms = 220) => new Promise((r) => setTimeout(r, ms))

const agents: Agent[] = [
  { id: '豆包管家', name: '豆包管家', scopes: ['read', 'write', 'live'] },
  { id: '小爱同学', name: '小爱同学', scopes: ['read', 'write'] },
]

let authCodes: AuthCode[] = [
  { code: '729104', kind: 'long', created_at: Date.now() / 1000 - 86400, expires_at: null, revoked: false },
]

let pairAccepting = true

const mk = (
  id: string,
  name: string,
  agent: string,
  preview_nl: string,
  devices: string[],
  enabled = true,
  trialState = 'auto',
  anomaly = false,
): Automation => ({
  id,
  name,
  agent,
  preview_nl,
  devices: devices.map((d) => ({ entity_id: d, friendly_name: d })),
  enabled,
  archived: false,
  status: enabled ? 'enabled' : 'disabled',
  pending_op_id: null,
  saved_at: new Date().toISOString(),
  version: 3,
  trial: { state: trialState, since: null, anomaly },
  last_triggered: anomaly ? null : new Date(Date.now() - 3600_000).toISOString(),
  trigger_7d: anomaly ? 0 : 12,
})

const automations: Automation[] = [
  mk(
    '夜间起夜灯',
    '夜间起夜灯',
    '豆包管家',
    '当卧室有人移动且环境光低于 5lux 时，自动开启夜灯 30% 亮度 90 秒',
    ['motion.bedroom', 'light.bedroom_night'],
    true,
    'auto',
    false,
  ),
  mk(
    '回家开空调',
    '回家开空调',
    '豆包管家',
    '当大门门锁在 18:00–23:00 开启且室外温度高于 30℃ 时，开启客厅空调 26℃',
    ['lock.front_door', 'climate.livingroom', 'sensor.outdoor_temp'],
    true,
    'canary',
    true,
  ),
  mk(
    '睡前播报',
    '睡前播报',
    '小爱同学',
    '每天 22:30 播报明日天气与明日行程',
    ['media_player.xiaomi'],
    false,
    'auto',
    false,
  ),
]

const pending: PendingItem[] = []

export const mockApi: Api = {
  getAgents: async () => {
    await delay()
    return { ok: true, agents: structuredClone(agents) }
  },
  deleteAgent: async (id: string) => {
    await delay()
    const i = agents.findIndex((a) => a.id === id)
    if (i >= 0) agents.splice(i, 1)
    return { ok: true, deleted: id }
  },
  renameAgent: async (id: string, name: string) => {
    await delay()
    const a = agents.find((x) => x.id === id)
    if (a) a.name = name
    return { ok: true, renamed: 1, from: id, to: name }
  },

  listAuthCodes: async () => {
    await delay()
    return { ok: true, codes: structuredClone(authCodes) }
  },
  createAuthCode: async (kind: string, ttl_minutes?: number) => {
    await delay()
    const code: AuthCode = {
      code: String(Math.floor(100000 + Math.random() * 899999)),
      kind,
      created_at: Date.now() / 1000,
      expires_at: kind === 'long' ? null : Date.now() / 1000 + (ttl_minutes || 5) * 60,
      revoked: false,
    }
    authCodes = [code, ...authCodes]
    return { ok: true, code: code.code, kind, expires_at: code.expires_at }
  },
  revokeAuthCode: async (code: string) => {
    await delay()
    authCodes = authCodes.filter((c) => c.code !== code)
    return { ok: true, revoked: code }
  },

  listAutomations: async () => {
    await delay()
    const groups: AutomationGroup[] = []
    const byAgent = new Map<string, Automation[]>()
    for (const a of automations) {
      const list = byAgent.get(a.agent) || []
      list.push(a)
      byAgent.set(a.agent, list)
    }
    for (const [agent, items] of byAgent) groups.push({ agent, items })
    return { ok: true, group_by: 'agent', groups, total: automations.length }
  },
  getAutomation: async (name: string) => {
    await delay()
    const a = automations.find((x) => x.name === name)
    if (!a) throw new Error('未找到自动化')
    return { ok: true, automation: structuredClone(a) }
  },
  enableAutomation: async (name: string) => {
    await delay()
    const a = automations.find((x) => x.name === name)
    if (a) a.enabled = true
    return { ok: true, name, enabled: true }
  },
  disableAutomation: async (name: string) => {
    await delay()
    const a = automations.find((x) => x.name === name)
    if (a) a.enabled = false
    return { ok: true, name, enabled: false }
  },
  archiveAutomation: async (name: string) => {
    await delay()
    const a = automations.find((x) => x.name === name)
    if (a) a.archived = true
    return { ok: true, name, archived: true }
  },
  unarchiveAutomation: async (name: string) => {
    await delay()
    const a = automations.find((x) => x.name === name)
    if (a) a.archived = false
    return { ok: true, name, archived: false }
  },
  deleteAutomation: async (name: string) => {
    await delay()
    const i = automations.findIndex((x) => x.name === name)
    if (i >= 0) automations.splice(i, 1)
    return { ok: true, deleted: name }
  },

  listPending: async () => {
    await delay()
    return { ok: true, ops: structuredClone(pending) }
  },
  approvePending: async (op_id: string) => {
    await delay()
    const i = pending.findIndex((p) => p.op_id === op_id)
    if (i >= 0) pending.splice(i, 1)
    return { ok: true, op_id }
  },
  rejectPending: async (op_id: string) => {
    await delay()
    const i = pending.findIndex((p) => p.op_id === op_id)
    if (i >= 0) pending.splice(i, 1)
    return { ok: true, op_id }
  },

  openPairStream: (onEvent: (d: PairEvent) => void): { close: () => void } => {
    const timer = setTimeout(() => {
      onEvent({ code: '48291307', agent_name_hint: '新 Agent', expires_at: Date.now() / 1000 + 300 })
    }, 4500)
    return { close: () => clearTimeout(timer) }
  },

  getPairAccepting: async (): Promise<PairAcceptingState> => {
    await delay()
    return { accepting: pairAccepting }
  },
  setPairAccepting: async (accepting: boolean): Promise<PairAccepting> => {
    await delay()
    pairAccepting = accepting
    return { ok: pairAccepting === accepting, accepting }
  },
}
