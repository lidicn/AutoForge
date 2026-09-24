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
}

// ===== Pending =====
export interface PendingItem {
  op_id: string
  summary: string
  agent_name: string
  submitted_at: string
  blast_radius: string
}

