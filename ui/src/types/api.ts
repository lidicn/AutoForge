export interface HealthResponse {
  ok: boolean
  version: string
  milestones: string[]
}

export interface GraphItem {
  name: string
  latest_version: number
  saved_at: string
  note: string
  /** 最新版本的 automation mode；多自动化归档为 `multi` */
  mode: string
  /** 归档名 → 自动化 id 映射（置信度接口以 automation_id 为键） */
  automation_ids: string[]
  /** v0.6.0 标签体系：本归档所属标签 */
  tags?: string[]
}

export interface GraphListResponse {
  items: GraphItem[]
}

export interface Node {
  id: string
  kind: 'on' | 'if' | 'do' | 'ask' | 'wait' | 'set' | 'pass'
  name: string
  trigger?: Record<string, unknown>
  expr?: Record<string, unknown>
  adapter?: string
  action?: string
  params?: Record<string, unknown>
  prompt?: string
  session?: string
  room?: string
  timeout?: string
  requires_confirm?: boolean
  canary?: Record<string, unknown>
  result_var?: string
  for?: string
}

export interface Edge {
  from: string
  to: string
  kind: 'then' | 'no' | 'yes' | 'on_error' | 'on_timeout' | 'on_cancel' | 'default'
}

export interface ExpectItem {
  entity_id?: string
  state?: string | string[]
  attribute?: string
  var?: string
  op?: 'eq' | 'ne' | 'lt' | 'lte' | 'gt' | 'gte'
  value?: unknown
  note?: string
}

export interface IR {
  ir_version: string
  id: string
  name: string
  version: number
  mode?: string
  snapshot?: boolean
  meta?: Record<string, unknown>
  nodes: Node[]
  edges: Edge[]
  expect?: ExpectItem[]
}

export interface Diagnostic {
  code: string
  level: 'error' | 'warning'
  message: string
  automation_id: string
  node_id: string
}

export interface GraphResponse {
  name: string
  version: number
  ir: IR
  nl: string
  diagnostics: Diagnostic[]
}

export interface BuildResponse {
  ok: boolean
  errors: Diagnostic[]
  warnings: Diagnostic[]
  nl: string
}

export interface Instance {
  instance_id: string
  state: 'created' | 'active' | 'suspended' | 'done' | 'cancelled' | 'failed' | 'expired'
  current_node: string
  trace: Array<{ node: string; at: string }>
}

export interface AuditEntry {
  type: 'entity_drift' | 'action_failed' | 'breaker_open' | 'breaker_recover' | 'quota_exceeded' | 'instance_rejected' | 'instance_expired'
  entity_id?: string
  message: string
  at: string
}

export interface SimResponse {
  instances: Instance[]
  audit: AuditEntry[]
  bus: Record<string, unknown>
  final_states: Record<string, string>
  nl: string
}

export interface ConfidenceItem {
  automation_id: string
  confidence: number
  band: 'auto' | 'shadow' | 'ask'
}

export interface Thresholds {
  auto: number
  shadow_low: number
}

export interface ConfResponse {
  items: ConfidenceItem[]
  thresholds: Thresholds
}

export interface DiffNode {
  node_id: string
  address: string
  automation_id: string
  node: string
}

export interface DiffEdge {
  automation_id: string
  from: string
  to: string
  kind: string
  edge: string
}

export interface DiffStructured {
  added_automations: string[]
  removed_automations: string[]
  added_nodes: DiffNode[]
  removed_nodes: DiffNode[]
  changed_nodes: Array<{ node_id: string; address: string; changes: string[] }>
  added_edges: DiffEdge[]
  removed_edges: DiffEdge[]
  meta_changes: Array<{ key: string; automation_id: string; field: string; old_value: unknown; new_value: unknown }>
}

export interface DiffResponse {
  render: string
  /** 两版本的归档备注（`note` 属归档元数据，不参与图内容比对） */
  notes?: { old: string; new: string }
  structured: DiffStructured
}

export interface SpecCompileResponse {
  ok: boolean
  ir: IR
  nl: string
  diagnostics: Diagnostic[]
}

export interface SpecResponse {
  spec: string
}

export interface FaultKind {
  value: string
  label: string
  inject_layer: string
  expected_handler: string
}

export interface FailureKind {
  key: string
  label: string
  edge: string
  recoverable: boolean
}

export interface FaultsResponse {
  kinds: FaultKind[]
  failures: FailureKind[]
}

export interface ApiError {
  code: string
  message: string
}

// ── v1.1.0 设备目录 / 实体解析（模块 D）────
export interface CatalogEntity {
  entity_id: string
  friendly_name: string
  domain: string
  area: string
  device_id: string
  platform: string
  integration: string
  integration_source: string
  offline_now: boolean
  connectivity_tier: string
  health_note: string
  state: string | null
  possible_states: string[]
  services: string[]
  high_risk: boolean
  matched_by: string
  confidence: string
}

export interface CatalogDevice {
  device_id: string
  area: string
  preferred_entity_id: string
  preferred_tier: string
  paths: Array<{
    entity_id: string
    domain: string
    integration: string
    connectivity_tier: string
    offline_now: boolean
  }>
}

export interface CatalogSnapshot {
  ok: boolean
  total_entities: number
  by_domain: Record<string, number>
  areas: string[]
  freshness: string
  ha_url: string
  note: string
}

export interface ResolveResponse {
  ok: boolean
  query: string
  area?: string
  area_warning?: string
  domain?: string
  count: number
  candidates: CatalogEntity[]
  devices: CatalogDevice[]
  note: string
  bucket?: string
  disambiguation?: {
    hint: string
    candidates: Array<{ entity_id: string; domain: string; friendly_name: string; why: string }>
  }
}

export interface EntityListResponse {
  ok: boolean
  entities: CatalogEntity[]
  returned: number
  matched_count: number
  truncated: boolean
  offset: number
  next_offset: number | null
  total: number
  freshness: string
  area_resolved?: string | null
  area_warning?: string | null
}

export interface EntityStateResponse {
  ok: boolean
  entity_id: string
  source: 'live' | 'catalog_cache'
  state: string | null
  domain: string
  friendly_name: string
  possible_states: string[]
  note?: string
  error?: string
  hint?: string
  freshness?: string
}

export interface AliasesResponse {
  ok: boolean
  total: number
  aliases: Record<string, string>
  path: string
  error?: string
}

export interface ResolveMetricsResponse {
  ok: boolean
  total: number
  buckets: { exact: number; medium: number; low: number; ambiguous: number; none: number }
  success_rate: number | null
  updated_at: string
}

export interface CatalogRefreshResponse {
  ok: boolean
  added: number
  changed: number
  removed: number
  total: number
  freshness: string
  ha_url: string
  note: string
  error?: string
  hint?: string
}

// ── v0.6.0 标签体系 + 批量启停（模块 B 增强）────
export interface GraphTagsResponse {
  ok: boolean
  name: string
  tags: string[]
}

export interface EnableByTagResponse {
  ok: boolean
  tag: string
  affected: string[]
}

// ── v0.7.0 备份导入/导出（模块 K）────
export interface StoreExportResponse {
  ok: boolean
  format?: string
  version?: string
  checksum?: string
  exported_at?: string
  names: string[]
  bundle: unknown
}

export interface StoreImportResponse {
  ok: boolean
  imported?: number
  skipped?: number
  renamed?: number
  errors?: number
  blast_radius?: { affected: number; limit: number }
  error?: string
}

// ── v1.6.0 绑定（模块 C 增强）────
export interface BindResult {
  ok: boolean
  ir: unknown
  bound: Array<{ node: string; field: string; from: string; to: string; matched_by: string }>
  unresolved: Array<{ node: string; field: string; from: string; reason: string }>
  total: number
  note: string
}

// ── v1.7.0-b 治理与实时（批次 B）────
// F 待批队列
export interface PendingItem {
  op_id: string
  tool: string
  payload: Record<string, unknown>
  summary: string
  blast_radius: { affected?: number; limit?: number; [k: string]: unknown }
  submitted_by: string
  submitted_at: string
  agent: string
}

export interface PendingListResponse {
  ok: boolean
  items: PendingItem[]
}

export interface ApproveResponse {
  ok: boolean
  approved_by: string
  pending: string
  [k: string]: unknown
}

export interface RejectResponse {
  ok: boolean
  rejected: string
  reason: string
}

// H 凭据
export interface CredentialsResponse {
  ha_token: string
  api_token: string
  connection_revision: number
}

export interface CredentialsUpdateResponse {
  ok: boolean
  connection_revision?: number
  [k: string]: unknown
}

// I 令牌
export interface WhoamiResponse {
  ok: boolean
  subject: string
  scopes: string[]
}

export interface SubjectItem {
  subject: string
  scopes: string[]
}

export interface SubjectsResponse {
  ok: boolean
  subjects: SubjectItem[]
}

export interface RevokeResponse {
  ok: boolean
  revoked: boolean
}

// G 真机下发
export interface LiveStatusResponse {
  ok: boolean
  enabled: boolean
  ha_url: string
  has_token: boolean
  allowlist_required: boolean
  confirm_required: boolean
  reasons: string[]
}

export interface LiveRunResponse {
  ok: boolean
  mode: string
  live: boolean
  ha_url: string
  allowlist: string[]
  writes: string[]
  instances: Instance[]
  audit: AuditEntry[]
  final_states: Record<string, string>
  nl: string
  asks: unknown[]
}


export interface WatchInstance {
  owner: string
  acquired_at: string
  graph: string
  ha_url: string
  sidecar: string
  name: string
  automation_id: string
  triggers: string[]
  actions: string[]
  node_count: number
}

export interface WatchListResponse {
  ok: boolean
  watches: WatchInstance[]
  total: number
}

// ── v2 M3 结构化 Ask（原生控件 + clarify 流程）──
// 控件映射由后端 AskSpec.control() 输出，前端只做渲染，不复制 kind→widget 映射
export interface AskControlMeta {
  widget: 'select' | 'slider' | 'time_range' | 'entity_picker' | 'input'
  kind: string
  prompt?: string
  options?: string[]
  min?: number | null
  max?: number | null
  unit?: string | null
  entity_domain?: string
}

export interface AskSpecMeta {
  kind: string
  options?: string[]
  min?: number
  max?: number
  unit?: string
  entity_domain?: string
}

export interface AskItem {
  ask_id: string
  instance_id: string
  node_id: string
  room: string | null
  prompt: string
  automation_id: string
  session_id?: string
  spec?: AskSpecMeta | null
  control: AskControlMeta
}

export interface AsksResponse {
  ok: boolean
  total: number
  asks: AskItem[]
}

export interface AskAnswerResponse {
  ok: boolean
  inbox?: string
}

