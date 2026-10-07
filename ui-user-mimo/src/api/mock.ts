/**
 * AutoForge 用户端 · Mock 数据层
 * ------------------------------------------------------------------
 * §1 只给了数据结构，没给 HTTP 契约，因此这里先定义「客户端接口」ApiClient：
 * 真实后端就绪后另写一个实现替换 mockApi 即可，视图 / store / 单测契约不变。
 */
import type { Agent, AuthCode, Automation, PairRequest, PendingItem, User } from '../types/api.ts'
import { PAIR_CODE_LENGTH, PAIR_TTL_MS, isPairCode, randomDigits } from '../logic/pairing.ts'
import { SHORT_CODE_LENGTH, shortRemainMs, validateShortMinutes } from '../logic/authcodes.ts'

export type ApiErrorCode =
  | 'AUTH_FAILED' | 'AUTH_INVALID_INPUT'
  | 'NOT_FOUND' | 'INVALID_NAME' | 'INVALID_STATE' | 'INVALID_MINUTES' | 'BAD_REQUEST'
  | 'SHORT_CODE_EXISTS' | 'PAIR_INVALID' | 'PAIR_EXPIRED' | 'NETWORK_ERROR'

export function apiError (code: ApiErrorCode, message: string): Error & { code: ApiErrorCode } {
  const e = new Error(message) as Error & { code: ApiErrorCode }
  e.name = 'ApiError'
  e.code = code
  return e
}

export interface ApiClient {
  now (): number
  login (username: string, password: string): Promise<User>
  logout (): Promise<void>
  listAgents (): Promise<Agent[]>
  renameAgent (agentId: string, name: string): Promise<Agent>
  deleteAgent (agentId: string): Promise<void>
  createPairRequest (agentNameHint: string): Promise<PairRequest>
  resolvePair (code: string): Promise<Agent>
  listAutomations (): Promise<Automation[]>            // 含归档，客户端自行 splitByArchived
  setAutomationEnabled (id: string, enabled: boolean): Promise<Automation>
  setAutomationArchived (id: string, archived: boolean): Promise<Automation>
  deleteAutomation (id: string): Promise<void>
  listPending (): Promise<PendingItem[]>
  resolvePending (opId: string, approve: boolean): Promise<void>
  listAuthCodes (): Promise<AuthCode[]>
  createLongCode (): Promise<AuthCode>
  createShortCode (minutes: number): Promise<AuthCode>
  revokeAuthCode (code: string): Promise<void>
}

// ---------------- mock 运行时（时钟 / 延迟 / 注入失败） ----------------
interface Db {
  agents: Agent[]
  automations: Automation[]
  pendings: PendingItem[]
  authCodes: AuthCode[]
  pairRequests: PairRequest[]
}

let clockOffset = 0
let latency = 180
const failures = new Map<string, number>()

function now (): number { return Date.now() + clockOffset }
function iso (ms: number): string { return new Date(ms).toISOString() }
function agoMin (minutes: number): string { return iso(now() - minutes * 60_000) }
function aheadMin (minutes: number): string { return iso(now() + minutes * 60_000) }

function call<T> (method: string, run: () => T): Promise<T> {
  return new Promise((resolve, reject) => {
    setTimeout(() => {
      const left = failures.get(method) ?? 0
      if (left > 0) {
        failures.set(method, left - 1)
        reject(apiError('NETWORK_ERROR', `${method} 请求失败（mock 注入的网络异常）`))
        return
      }
      try { resolve(run()) } catch (e) { reject(e) }
    }, Math.max(0, latency))
  })
}

// ---------------- 种子数据 ----------------
const LONG_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'

function genLongCode (): string {
  const groups: string[] = []
  for (let g = 0; g < 3; g++) {
    let s = ''
    for (let i = 0; i < 4; i++) s += LONG_ALPHABET[Math.floor(Math.random() * LONG_ALPHABET.length)]
    groups.push(s)
  }
  return 'AF-' + groups.join('-')
}

function seed (): Db {
  const agents: Agent[] = [
    { agent_id: 'agt_living', name: '客厅主控', connected_at: agoMin(60 * 24 * 9), last_seen: agoMin(2) },
    { agent_id: 'agt_lab',    name: '实验沙箱', connected_at: agoMin(60 * 24 * 2), last_seen: agoMin(26 * 60) },
  ]

  const automations: Automation[] = [
    {
      id: 'auto_01', name: '夜间照明自动调节', agent_id: 'agt_living', agent_name: '客厅主控', status: 'enabled',
      preview_nl: '每天 22:00 之后把客厅灯光压到 30% 暖光；检测到有人移动时临时调亮到 60%，5 分钟无动作后回落。',
      devices: [
        { friendly_name: '客厅主灯', entity_id: 'light.living_main' },
        { friendly_name: '客厅人体传感器', entity_id: 'binary_sensor.living_motion' },
      ],
      last_triggered: agoMin(43), trigger_7d: 12, archived: false,
      trial: { state: 'auto', since: agoMin(60 * 24 * 6), anomaly: null },
    },
    {
      id: 'auto_02', name: '离家自动断电', agent_id: 'agt_living', agent_name: '客厅主控', status: 'enabled',
      preview_nl: '全屋无人且门锁上锁超过 10 分钟后，关闭排插、热水器与非必要照明。',
      devices: [
        { friendly_name: '客厅排插', entity_id: 'switch.living_strip' },
        { friendly_name: '入户门锁', entity_id: 'lock.front_door' },
      ],
      last_triggered: agoMin(60 * 19), trigger_7d: 4, archived: false,
      trial: { state: 'canary', since: agoMin(60 * 30), anomaly: null },
    },
    {
      id: 'auto_03', name: '湿度联动除湿机', agent_id: 'agt_lab', agent_name: '实验沙箱', status: 'enabled',
      preview_nl: '卫生间湿度连续 15 分钟高于 68% 时开启除湿机，回落到 55% 以下关闭。',
      devices: [
        { friendly_name: '卫生间湿度计', entity_id: 'sensor.bath_humidity' },
        { friendly_name: '除湿机', entity_id: 'switch.dehumidifier' },
      ],
      last_triggered: agoMin(60 * 7), trigger_7d: 6, archived: false,
      trial: { state: 'shadow', since: agoMin(60 * 20), anomaly: '昨日出现 3 次误触发，仍停留在影子观察期' },
    },
    {
      id: 'auto_04', name: '晨间播报', agent_id: 'agt_lab', agent_name: '实验沙箱', status: 'disabled',
      preview_nl: '工作日 07:20 播报天气、日程与昨日用电统计。',
      devices: [{ friendly_name: '书房音箱', entity_id: 'media_player.study_speaker' }],
      last_triggered: agoMin(60 * 24 * 3), trigger_7d: 0, archived: false,
      trial: { state: 'auto', since: agoMin(60 * 24 * 12), anomaly: null },
    },
    {
      id: 'auto_05', name: '门锁重复上锁看护', agent_id: 'agt_living', agent_name: '客厅主控', status: 'anomaly',
      preview_nl: '门锁在短时间内被反复上锁时提醒，并尝试关掉触发这条链路的自动化。',
      devices: [{ friendly_name: '入户门锁', entity_id: 'lock.front_door' }],
      last_triggered: agoMin(96), trigger_7d: 2, archived: false,
      trial: { state: 'canary', since: agoMin(60 * 5), anomaly: '4 分钟内重复上锁 7 次，已暂停执行等待人工确认' },
    },
    {
      id: 'auto_06', name: '周末扫地机排班', agent_id: 'agt_lab', agent_name: '实验沙箱', status: 'enabled',
      preview_nl: '每周六 10:00 启动扫地机，检测到有人回家则暂停并回充。',
      devices: [{ friendly_name: '扫地机', entity_id: 'vacuum.floor_bot' }],
      last_triggered: agoMin(60 * 24 * 5), trigger_7d: 1, archived: true,
      trial: { state: 'auto', since: agoMin(60 * 24 * 30), anomaly: null },
    },
    {
      id: 'auto_07', name: '新风与 CO₂ 联动', agent_id: 'agt_lab', agent_name: '实验沙箱', status: 'pending',
      preview_nl: 'CO₂ 高于 1000ppm 且持续 5 分钟时开启新风 2 档，回落到 800ppm 以下关闭。',
      devices: [
        { friendly_name: 'CO₂ 传感器', entity_id: 'sensor.lab_co2' },
        { friendly_name: '新风机', entity_id: 'fan.fresh_air' },
      ],
      last_triggered: null, trigger_7d: 0, archived: false,
      trial: { state: 'shadow', since: agoMin(12), anomaly: null },
    },
  ]

  const pendings: PendingItem[] = [
    {
      op_id: 'auto_07',
      summary: '新建自动化「新风与 CO₂ 联动」：CO₂ > 1000ppm 持续 5 分钟则开启新风 2 档',
      agent_name: '实验沙箱', submitted_at: agoMin(12),
      blast_radius: '中（涉及 2 个设备，可自动回滚）',
    },
    {
      op_id: 'op_102',
      summary: '把「夜间照明自动调节」的生效时间从 22:00 提前到 21:30',
      agent_name: '客厅主控', submitted_at: agoMin(3),
      blast_radius: 'low',
    },
  ]

  const authCodes: AuthCode[] = [
    { code: 'AF-4K2Q-9XRM-7TDV', type: 'long', created_at: agoMin(60 * 24 * 20), expires_at: null },
    { code: 'AF-P8LM-3RWX-K29C', type: 'long', created_at: agoMin(60 * 24 * 4), expires_at: null },
    { code: '482913', type: 'short', created_at: agoMin(3), expires_at: aheadMin(9) },
  ]

  return { agents, automations, pendings, authCodes, pairRequests: [] }
}

let db: Db = seed()

function purgeExpiredShort (codes: AuthCode[]): AuthCode[] {
  return codes.filter(c => c.type !== 'short' || shortRemainMs(c, now()) > 0)
}

export const mockApi: ApiClient = {
  now () { return now() },

  login (username, password) {
    return call('login', () => {
      const u = (username ?? '').trim()
      const p = (password ?? '').trim()
      if (!u || !p) throw apiError('AUTH_INVALID_INPUT', '用户名和密码不能为空')
      // 与后端 `/api/auth/login` 同语义：任意非空凭据签发单 owner 会话（轻量登录，无用户表）。
      // 原先这里比对一对写死的演示凭据并抛 AUTH_FAILED——那既不是后端的行为，又把
      // "这套系统有一对通用口令"印进了每一个克隆（裁定 20261004 §一 F-2 的 UI 半边）。
      const user: User = { username: u, role: 'admin' }
      return user
    })
  },

  logout () { return call('logout', () => undefined) },

  listAgents () {
    return call('listAgents', () =>
      [...db.agents].sort((a, b) => Date.parse(b.last_seen) - Date.parse(a.last_seen)))
  },

  renameAgent (agentId, name) {
    return call('renameAgent', () => {
      const next = (name ?? '').trim()
      if (!next) throw apiError('INVALID_NAME', '名称不能为空')
      if (next.length > 24) throw apiError('INVALID_NAME', '名称最长 24 个字符')
      const agent = db.agents.find(a => a.agent_id === agentId)
      if (!agent) throw apiError('NOT_FOUND', 'Agent 不存在或已被删除')
      agent.name = next
      for (const m of db.automations) if (m.agent_id === agentId) m.agent_name = next
      return agent
    })
  },

  deleteAgent (agentId) {
    return call('deleteAgent', () => {
      const before = db.agents.length
      db.agents = db.agents.filter(a => a.agent_id !== agentId)
      if (db.agents.length === before) throw apiError('NOT_FOUND', 'Agent 不存在或已被删除')
      // fail-open 于数据：其自动化只归档，不物理删除
      for (const m of db.automations) if (m.agent_id === agentId) m.archived = true
    })
  },

  createPairRequest (agentNameHint) {
    return call('createPairRequest', () => {
      let code = randomDigits(PAIR_CODE_LENGTH)
      while (db.pairRequests.some(p => p.code === code)) code = randomDigits(PAIR_CODE_LENGTH)
      const req: PairRequest = {
        agent_name_hint: (agentNameHint ?? '').trim() || '新 Agent',
        code,
        expires_at: iso(now() + PAIR_TTL_MS),
      }
      db.pairRequests.push(req)
      return req
    })
  },

  resolvePair (code) {
    return call('resolvePair', () => {
      const req = db.pairRequests.find(p => p.code === code)
      if (!req) throw apiError('PAIR_INVALID', '配对码无效')
      if (!isPairCode(code)) throw apiError('PAIR_INVALID', '配对码格式不正确')
      if (now() >= Date.parse(req.expires_at)) throw apiError('PAIR_EXPIRED', '配对码已过期，请重新生成')
      const agent: Agent = {
        agent_id: 'agt_' + Math.random().toString(36).slice(2, 8),
        name: req.agent_name_hint,
        connected_at: iso(now()),
        last_seen: iso(now()),
      }
      db.agents.unshift(agent)
      db.pairRequests = db.pairRequests.filter(p => p.code !== code)
      return agent
    })
  },

  listAutomations () { return call('listAutomations', () => [...db.automations]) },

  setAutomationEnabled (id, enabled) {
    return call('setAutomationEnabled', () => {
      const m = db.automations.find(x => x.id === id)
      if (!m) throw apiError('NOT_FOUND', '自动化不存在')
      if (m.status === 'pending') throw apiError('INVALID_STATE', '待批草稿请先在待批列表里批准')
      m.status = enabled ? 'enabled' : 'disabled'
      return m
    })
  },

  setAutomationArchived (id, archived) {
    return call('setAutomationArchived', () => {
      const m = db.automations.find(x => x.id === id)
      if (!m) throw apiError('NOT_FOUND', '自动化不存在')
      m.archived = archived
      return m
    })
  },

  deleteAutomation (id) {
    return call('deleteAutomation', () => {
      const before = db.automations.length
      db.automations = db.automations.filter(x => x.id !== id)
      if (db.automations.length === before) throw apiError('NOT_FOUND', '自动化不存在')
    })
  },

  listPending () { return call('listPending', () => [...db.pendings]) },

  // 约定：op_id 若与某个 Automation.id 相同，视为同一操作（见 §7 B.4-6）
  resolvePending (opId, approve) {
    return call('resolvePending', () => {
      const before = db.pendings.length
      db.pendings = db.pendings.filter(p => p.op_id !== opId)
      if (db.pendings.length === before) throw apiError('NOT_FOUND', '待批项不存在或已被处理')
      const m = db.automations.find(x => x.id === opId)
      if (m) {
        if (approve) m.status = 'enabled'
        else m.archived = true       // 驳回：草稿归档留痕，不物理删除
      }
    })
  },

  listAuthCodes () {
    return call('listAuthCodes', () => {
      db.authCodes = purgeExpiredShort(db.authCodes)     // 过期短期码读取时清理（fail-open）
      return [...db.authCodes].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    })
  },

  createLongCode () {
    return call('createLongCode', () => {
      let code = genLongCode()
      while (db.authCodes.some(c => c.code === code)) code = genLongCode()
      const item: AuthCode = { code, type: 'long', created_at: iso(now()), expires_at: null }
      db.authCodes.unshift(item)
      return item
    })
  },

  createShortCode (minutes) {
    return call('createShortCode', () => {
      const check = validateShortMinutes(minutes)
      if (!check.ok) throw apiError('INVALID_MINUTES', check.message)
      db.authCodes = purgeExpiredShort(db.authCodes)
      if (db.authCodes.some(c => c.type === 'short')) {
        throw apiError('SHORT_CODE_EXISTS', '已有一个短期码生效中（同时只能存在一个）')
      }
      const created = now()
      let code = randomDigits(SHORT_CODE_LENGTH)
      while (db.authCodes.some(c => c.code === code)) code = randomDigits(SHORT_CODE_LENGTH)
      const item: AuthCode = {
        code, type: 'short',
        created_at: iso(created),
        expires_at: iso(created + minutes * 60_000),
      }
      db.authCodes.unshift(item)
      return item
    })
  },

  revokeAuthCode (code) {
    return call('revokeAuthCode', () => {
      const before = db.authCodes.length
      db.authCodes = db.authCodes.filter(c => c.code !== code)
      if (db.authCodes.length === before) throw apiError('NOT_FOUND', '授权码不存在')
    })
  },

}

// ---------------- 测试注入点（不影响业务语义） ----------------
export function __reset (): void { db = seed(); failures.clear() }
export function __setLatency (ms: number): void { latency = ms }
export function __advanceClock (ms: number): void { clockOffset += ms }
export function __break (method: string, times = 1): void {
  failures.set(method, (failures.get(method) ?? 0) + times)
}