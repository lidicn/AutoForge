// ===== Auth =====
export interface User {
  username: string
  role: 'user' | 'admin'
}

// ===== Agent =====
export interface Agent {
  agent_id: string
  name: string
  connected_at: string | null
  last_seen: string | null
}

export interface PairRequest {
  agent_name_hint: string
  code: string
  expires_at: string
}

// ===== Automation =====
export type AutomationStatus = 'pending' | 'enabled' | 'disabled' | 'anomaly'

export type TrialState = 'auto' | 'shadow' | 'canary'

export interface DeviceRef {
  friendly_name: string
  entity_id: string
}

export interface TrialInfo {
  state: TrialState
  since: string
  anomaly: string | null
}

export interface Automation {
  id: string
  name: string
  agent_id: string
  agent_name: string
  status: AutomationStatus
  preview_nl: string
  devices: DeviceRef[]
  last_triggered: string | null
  trigger_7d: number
  archived: boolean
  trial: TrialInfo | null
}

// ===== Auth Code =====
export type AuthCodeType = 'long' | 'short'

export interface AuthCode {
  code: string
  type: AuthCodeType
  created_at: string
  expires_at: string | null
  // v2.6（gap #9）：consumed=true = 已被一次性消耗（短期码/历史废码），不得再当可用码展示（审计 F-21）；
  // use_count = 长期码累计使用次数（长期码不清码，只有 revoked 才失效）。
  consumed?: boolean
  use_count?: number
}

// ===== Pending =====
export interface PendingItem {
  op_id: string
  summary: string
  agent_name: string
  submitted_at: string
  blast_radius: string
}

