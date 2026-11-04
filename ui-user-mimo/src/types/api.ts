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

// 批 5：owner 面生成配对链接（用户先出码）
export interface PairLink {
  link: string
  code: string
  agent_name_hint: string
  expires_at: string
  ttl_s: number
}

// ===== Automation =====
export type AutomationStatus = 'pending' | 'enabled' | 'disabled' | 'anomaly'

export type TrialState = 'auto' | 'shadow' | 'canary'

// 后端 `af_runtime_facts.record_trial` 落盘的是**另一套词表**（2026-11-04 对接）：
// observing（canary 观察窗内）/ verified（试演通过转正）/ rolled_back（回滚）/ failed。
// ⛔ 不把两套词表并成一条 union：UI 三段放权是产品语义，后端状态是运行事实，映射在 logic 层做。
export type TrialRuntimeState = 'observing' | 'verified' | 'rolled_back' | 'failed'

export interface DeviceRef {
  friendly_name: string
  entity_id: string
}

export interface TrialInfo {
  state: TrialState | TrialRuntimeState
  since: string
  /** 观察窗截止（后端 `trial.until`，只在有观察窗时才给）；⛔ 别拿 since+固定值自己算 */
  until?: string | null
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
  // 2026-11-04：作废标记（后端 `/api/user/auth-codes` 一直在返回，前端此前没类型化）。
  revoked?: boolean
  // 后端派生态（AF-Core `d1fb08e` 起）：前端优先采信；缺失时按 consumed/revoked/到期兜底。
  status?: 'active' | 'consumed' | 'expired' | 'revoked'
  // 消耗时刻（后端补），有则显示「已消耗于 <时刻>」；无则只显示「已消耗」。
  consumed_at?: string | null
}

// ===== Pending =====
export interface PendingItem {
  op_id: string
  summary: string
  agent_name: string
  submitted_at: string
  blast_radius: string
  /**
   * AF-726：交付面待批列表也要在**批准之前**看见本条含哪些 L2 动作（与管理员面 `ui/` 对齐）。
   * 由 `logic/pending.ts` 归一，**三态可区分**：非空数组＝含这些 L2；空数组＝本条不含 L2；
   * `null`／缺字段＝未读到（后端旧版）——⛔ 不许把缺失归一成空数组。
   */
  l2_summary?: L2Action[] | null
}

/** DCD 裁定 20261031·矩阵行 2：批准回执里的 L2 动作条目（后端 `l2_summary_of` 机提取，前端只渲染）。 */
export interface L2Action {
  action: string
  alias: string
  automation: string
  node: string
}

/** `approve_pending` 回执（v1.15.0 卡 B）：空数组＝本条不含 L2；缺字段＝后端没给（⛔ 别当成空数组）。 */
export interface PendingReceipt {
  ok: boolean
  l2_summary?: L2Action[]
}

