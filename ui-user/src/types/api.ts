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
