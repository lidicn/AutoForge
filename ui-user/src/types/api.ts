export interface Agent {
  id: string
  name: string
  scopes: string[]
}

export interface AuthCode {
  code: string
  kind: string // "long" | "short"
  created_at: number
  expires_at: number | null
  revoked: boolean
}

export interface DeviceRef {
  entity_id: string | null
  friendly_name: string | null
}

export interface Trial {
  state: string // "auto" | "shadow" | "canary"
  since: string | null
  anomaly: boolean
}

export interface Automation {
  id: string
  name: string
  agent: string
  preview_nl: string
  devices: DeviceRef[]
  enabled: boolean
  archived: boolean
  status: string // "enabled" | "disabled" | "archived"
  pending_op_id?: string | null
  saved_at?: string
  version?: number
  trial: Trial
  last_triggered: string | null
  trigger_7d: number
}

export interface AutomationGroup {
  agent: string
  items: Automation[]
}

export interface PendingItem {
  op_id: string
  tool: string
  submitted_by?: string
  created_at?: string
  payload?: { name?: string; note?: string; ir?: unknown }
  reason?: string
}

/** DCD 裁定 20261031·矩阵行 2：批准回执里的 L2 动作条目（后端 `l2_summary_of` 机提取，前端只渲染）。 */
export interface L2Action {
  action: string
  alias: string
  automation: string
  node: string
}

/** `approve_pending` 回执（v1.15.0 卡 B）：空数组＝本条不含 L2；缺字段＝后端没给（⛔ 别当成空数组）。 */
export interface PendingApproveResponse {
  ok: boolean
  l2_summary?: L2Action[]
  [k: string]: unknown
}

export interface PairEvent {
  code: string
  agent_name_hint: string
  expires_at: number
}

/** GET 现值：只读盘点不回 `ok`（后端 AST 门禁 fake-ok-const 的仓内口径）。 */
export interface PairAcceptingState {
  accepting: boolean
}

/** POST 新值：`ok` 是「写入后回读 == 请求」这一实际校验的读数。 */
export interface PairAccepting extends PairAcceptingState {
  ok: boolean
}
