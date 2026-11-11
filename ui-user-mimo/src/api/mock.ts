/**
 * AutoForge 用户端 · Mock 数据层
 * ------------------------------------------------------------------
 * §1 只给了数据结构，没给 HTTP 契约，因此这里先定义「客户端接口」ApiClient：
 * 真实后端就绪后另写一个实现替换 mockApi 即可，视图 / store / 单测契约不变。
 */
import type { Agent, AuthCode, Automation, Grant, GrantListResponse, GrantReceipt, GrantReceiptsResponse, Order, OrderListResponse, OrderReceipt, OrderReceiptsResponse, PairLink, PairRequest, PendingItem, PendingReceipt, User } from '../types/api.ts'
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
  createPairLink (agentNameHint: string): Promise<PairLink>
  resolvePair (code: string): Promise<Agent>
  listAutomations (): Promise<Automation[]>            // 含归档，客户端自行 splitByArchived
  setAutomationEnabled (id: string, enabled: boolean): Promise<Automation>
  setAutomationArchived (id: string, archived: boolean): Promise<Automation>
  deleteAutomation (id: string): Promise<void>
  listPending (): Promise<PendingItem[]>
  resolvePending (opId: string, approve: boolean): Promise<PendingReceipt>
  listAuthCodes (): Promise<AuthCode[]>
  createLongCode (): Promise<AuthCode>
  createShortCode (minutes: number): Promise<AuthCode>
  revokeAuthCode (code: string): Promise<void>
  // v1.29 执行授权（grant）：列表/撤销/留痕——判定真源在后端，前端只渲染
  listGrants (): Promise<GrantListResponse>
  revokeGrant (grantId: string, reason: string): Promise<void>
  listGrantReceipts (limit?: number): Promise<GrantReceiptsResponse>
  // v1.29 任务单（TaskOrder）：活动池/逐单留痕/取消
  listOrders (): Promise<OrderListResponse>
  listOrderReceipts (limit?: number): Promise<OrderReceiptsResponse>
  cancelOrder (orderId: string, reason: string): Promise<void>
}

// ---------------- mock 运行时（时钟 / 延迟 / 注入失败） ----------------
interface Db {
  agents: Agent[]
  automations: Automation[]
  pendings: PendingItem[]
  authCodes: AuthCode[]
  pairRequests: PairRequest[]
  grants: Grant[]
  grantReceipts: GrantReceipt[]
  orders: Order[]
  orderReceipts: OrderReceipt[]
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
    { agent_id: 'agt_living', name: '客厅主控', connected_at: agoMin(60 * 24 * 9), last_seen: agoMin(2), scopes: ['read', 'write', 'live'] },
    { agent_id: 'agt_lab',    name: '实验沙箱', connected_at: agoMin(60 * 24 * 2), last_seen: agoMin(26 * 60), scopes: ['read'] },
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
      l2_summary: [],   // 假后端没有 IR 判定能力 ⇒ 诚实给空数组（视图显式占位）
    },
    {
      op_id: 'op_102',
      summary: '把「夜间照明自动调节」的生效时间从 22:00 提前到 21:30',
      agent_name: '客厅主控', submitted_at: agoMin(3),
      blast_radius: 'low',
      l2_summary: [],
    },
  ]

  const authCodes: AuthCode[] = [
    { code: 'AF-4K2Q-9XRM-7TDV', type: 'long', created_at: agoMin(60 * 24 * 20), expires_at: null, consumed: false, use_count: 0 },
    { code: 'AF-P8LM-3RWX-K29C', type: 'long', created_at: agoMin(60 * 24 * 4), expires_at: null, consumed: false, use_count: 3 },
    { code: '482913', type: 'short', created_at: agoMin(3), expires_at: aheadMin(9), consumed: false, use_count: 0 },
  ]

  // v1.29 执行授权 fixtures：三态各一枚（活／已撤销／已过期），⛔ 不含任何码值
  const grants: Grant[] = [
    {
      grant_id: 'g-1a2b3c4d5e6f', agent: 'agt_living', rooms: ['study'],
      device_scope: { include: ['light.study_main', 'climate.study_ac'] },
      action_risk: 'low', expires_at: Math.floor(now() / 1000) + 6 * 86400 + 3600,
      revoke: { revoked: false, reason: '', at: '' },
      issued_at: agoMin(60 * 24 * 3), issued_by: 'owner', issued_via: 'mimo', ttl_days: 7, uses: 2,
    },
    {
      grant_id: 'g-9f8e7d6c5b4a', agent: 'agt_lab', rooms: ['all'],
      device_scope: { include: [] },
      action_risk: 'read', expires_at: Math.floor(now() / 1000) + 20 * 86400,
      revoke: { revoked: true, reason: '演示撤销', at: agoMin(90) },
      issued_at: agoMin(60 * 24 * 12), issued_by: 'owner', issued_via: 'mimo', ttl_days: 30, uses: 5,
    },
    {
      grant_id: 'g-5c4b3a2918f7', agent: 'agt_living', rooms: ['zhu_wo_shi'],
      device_scope: { include: ['light.living_main'] },
      action_risk: 'l2', expires_at: Math.floor(now() / 1000) - 5 * 3600,
      revoke: { revoked: false, reason: '', at: '' },
      issued_at: agoMin(60 * 24 * 9), issued_by: 'owner', issued_via: 'auth_code', ttl_days: 7, uses: 12,
    },
  ]

  const grantReceipts: GrantReceipt[] = [
    { type: 'GRANT_ISSUED', at: agoMin(60 * 24 * 3), grant: { grant_id: 'g-1a2b3c4d5e6f' }, agent: 'agt_living', summary: '签发执行授权（7 天）', what: [], risk: 'low' },
    { type: 'LIVE_RUN_EXECUTED', at: agoMin(42), grant: { grant_id: 'g-1a2b3c4d5e6f' }, agent: 'agt_living', summary: '书房空调 → 制冷 26℃', what: ['climate.study_ac'], risk: 'low' },
    { type: 'GRANT_REVOKED', at: agoMin(90), grant: { grant_id: 'g-9f8e7d6c5b4a' }, agent: 'agt_lab', summary: '撤销执行授权：演示撤销', what: [], risk: 'read' },
  ]

  // v1.29 任务单 fixtures：待执行 + 执行中（活动池里只会有这两态；终态即销）
  const orders: Order[] = [
    {
      order_id: 'o-a1b2c3d4e5f6', agent: 'agt_living', created_at: agoMin(5), created_by: 'agt_living',
      due_at: Math.floor(now() / 1000) + 25 * 60, due_kind: 'delay',
      steps: [
        { adapter: 'ha', action: 'light.turn_on', params: { entity_id: 'light.living_main' } },
        { adapter: 'ha', action: 'climate.set_temperature', params: { entity_id: 'climate.study_ac' } },
      ],
      output: { channels: ['tts', 'mimo'], room: 'zhu_wo_shi', text: '你订的两步任务做完了', kind: 'control' },
      grant_id: 'g-1a2b3c4d5e6f', status: 'pending',
      receipt: { steps: [], output: [] }, started_at: '', finished_at: '', cancel: {},
    },
    {
      order_id: 'o-9f8e7d6c5b4a', agent: 'agt_living', created_at: agoMin(12), created_by: 'agt_living',
      due_at: Math.floor(now() / 1000) - 60, due_kind: 'at',
      steps: [
        { adapter: 'ha', action: 'switch.turn_off', params: { entity_id: 'switch.study_lamp' } },
      ],
      output: { channels: ['tts'], room: 'shu_fang', text: '台灯关好了', kind: 'control' },
      grant_id: 'g-1a2b3c4d5e6f', status: 'running',
      receipt: { steps: [], output: [] }, started_at: agoMin(1), finished_at: '', cancel: {},
    },
  ]

  const orderReceipts: OrderReceipt[] = [
    { type: 'TASK_ORDER_CREATED', at: agoMin(12), order_id: 'o-9f8e7d6c5b4a', agent: 'agt_living', status: 'pending', reason: '', steps: [] },
    { type: 'TASK_ORDER_FINISHED', at: agoMin(150), order_id: 'o-7c6b5a493827', agent: 'agt_living', status: 'done', reason: '', steps: [{ action: 'light.turn_on', status: 'done' }] },
    { type: 'TASK_ORDER_CANCELLED', at: agoMin(300), order_id: 'o-3e2d1c0b9a87', agent: 'agt_lab', status: 'cancelled', reason: '人不在家，改主意了', steps: [] },
  ]

  return {
    agents, automations, pendings, authCodes, pairRequests: [],
    grants, grantReceipts, orders, orderReceipts,
  }
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

  createPairLink (agentNameHint) {
    // 与真后端 POST /api/user/pair-link 同形状（返工 5.2 补齐：批 5 只写了 http 侧，
    // mock 漏了这个方法 ⇒ mock 模式点「生成配对链接」直接 undefined is not a function）。
    // ttl 对齐后端 pair-link 径的 600s（出资人 Q6）；⛔ 不复用老码径的 300s 常数。
    return call('createPairLink', () => {
      const ttlS = 600
      const code = randomDigits(8)   // 后端 _rand6 实际 8 位（函数名历史遗留）
      const hint = (agentNameHint ?? '').trim() || '新 Agent'
      const link: PairLink = {
        link: `http://autoforge.mock/mimo/pair?k=${randomDigits(43)}`,  // 43 字符，与真链 link_key 同形
        code,
        agent_name_hint: hint,
        expires_at: iso(now() + ttlS * 1000),
        ttl_s: ttlS,
      }
      return link
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
      // v1.15.0 卡 B：真实后端按 IR 机提取 L2 清单；mock 没有 IR 判定能力，
      // 批准按「本条不含 L2」的空数组回（空数组是合法形态，视图要显式占位）；驳回无此字段。
      return approve ? { ok: true, l2_summary: [] } : { ok: true }
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
      const item: AuthCode = { code, type: 'long', created_at: iso(now()), expires_at: null, consumed: false, use_count: 0 }
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

  // ---------------- v1.29 执行授权 / 任务单（mock：只做形状与终态语义，⛔ 不模拟 grant 判定） ----------------

  listGrants () {
    return call('listGrants', () => {
      const items = [...db.grants].sort((a, b) => String(b.issued_at).localeCompare(String(a.issued_at)))
      return { ok: true, items, count: items.length }
    })
  },

  revokeGrant (grantId, reason) {
    return call('revokeGrant', () => {
      const g = db.grants.find(x => x.grant_id === grantId)
      if (!g) throw apiError('NOT_FOUND', '执行授权不存在')
      if (g.revoke.revoked) throw apiError('INVALID_STATE', '该授权已撤销')
      g.revoke = { revoked: true, reason: (reason || '').trim(), at: iso(now()) }
      db.grantReceipts.unshift({
        type: 'GRANT_REVOKED', at: g.revoke.at, grant: { grant_id: g.grant_id },
        agent: g.agent, summary: '撤销执行授权' + (reason ? `：${reason}` : ''), what: [], risk: g.action_risk,
      })
    })
  },

  listGrantReceipts (limit = 20) {
    return call('listGrantReceipts', () => {
      const items = [...db.grantReceipts].slice(0, Math.max(1, limit))
      return { ok: true, count: items.length, items }
    })
  },

  listOrders () {
    return call('listOrders', () => ({ ok: true, items: [...db.orders], count: db.orders.length }))
  },

  listOrderReceipts (limit = 20) {
    return call('listOrderReceipts', () => {
      const items = [...db.orderReceipts].slice(0, Math.max(1, limit))
      return { ok: true, count: items.length, items }
    })
  },

  cancelOrder (orderId, reason) {
    return call('cancelOrder', () => {
      const idx = db.orders.findIndex(o => o.order_id === orderId)
      if (idx < 0) throw apiError('NOT_FOUND', '任务单不存在或已终态')
      const o = db.orders[idx]
      db.orders.splice(idx, 1)       // 终态即销：活动池里不再有它，只剩留痕
      db.orderReceipts.unshift({
        type: 'TASK_ORDER_CANCELLED', at: iso(now()), order_id: o.order_id, agent: o.agent,
        status: 'cancelled', reason: (reason || '').trim(), steps: [],
      })
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