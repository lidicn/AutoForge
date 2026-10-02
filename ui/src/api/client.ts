import type {
  HealthResponse, GraphListResponse, GraphResponse, BuildResponse,
  SimResponse, ConfResponse, DiffResponse, SpecCompileResponse, SpecResponse, FaultsResponse,
  CatalogSnapshot, CatalogRefreshResponse, ResolveResponse, EntityListResponse, EntityStateResponse,
  AliasesResponse, ResolveMetricsResponse, GraphTagsResponse, EnableByTagResponse,
  StoreExportResponse, StoreImportResponse, BindResult,
  PendingListResponse, ApproveResponse, RejectResponse,
  CredentialsResponse, CredentialsUpdateResponse,
  WhoamiResponse, SubjectsResponse, RevokeResponse,
  LiveStatusResponse, LiveRunResponse,
  UndoAvailableResponse, UndoPreviewResponse, UndoRunResponse,
  EvidenceProdResponse, WatchListResponse,
  AsksResponse, AskAnswerResponse,
  MetricsResponse, ExperienceResponse, TelemetryResponse, SessionViewResponse,
  InsightsPendingResponse, InsightApproveResponse, InsightRejectResponse,
} from '../types/api'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8787/api'

function _authHeader(): Record<string, string> {
  // 生产环境（AUTOFORGE_TOKENS 已配置）下，写/live 操作需带 Bearer 令牌。
  // 令牌存于 localStorage('af_token')，由「治理 / 设置」页填写；未配置则留空。
  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('af_token') : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(method: string, path: string, body?: unknown): Promise<{ data: T }> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ..._authHeader() },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await _detail(res)}`)
  // 真后端返回**扁平**对象（无 { data } 外壳）；此处统一包装成 { data }，
  // 与 mock 层保持一致，视图侧无需改动。
  const json = (await res.json()) as T
  return { data: json }
}

/** 失败原因在响应体的 `detail` 里（403/404/409/422 都是）。只报 statusText
 * 等于把"为什么被拒"咽下——审批类面板必须把后端原话显示给人。 */
async function _detail(res: Response): Promise<string> {
  const text = await res.text().catch(() => '')
  if (!text) return res.statusText || '无响应体'
  try {
    const parsed = JSON.parse(text) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
    if (parsed.detail !== undefined) return JSON.stringify(parsed.detail)
  } catch {
    /* 非 JSON（网关错误页等）就原样显示 */
  }
  return text.slice(0, 300)
}

export const api = {
  health: () => request<HealthResponse>('GET', '/health'),
  graphs: () => request<GraphListResponse>('GET', '/graphs'),
  graph: (name: string, version?: string) =>
    request<GraphResponse>('GET', `/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}`),
  build: (ir: unknown) => request<BuildResponse>('POST', '/build', { ir }),
  sim: (ir: unknown, seed?: Record<string, string>, events?: unknown[]) =>
    request<SimResponse>('POST', '/sim', { ir, seed, events }),
  conf: (name: string) => request<ConfResponse>('GET', `/conf/${encodeURIComponent(name)}`),
  intervene: (name: string, automation_id: string) =>
    request<ConfResponse>('POST', `/conf/${encodeURIComponent(name)}/intervene`, { automation_id }),
  diff: (name: string, old: string, new_v: string) =>
    request<DiffResponse>('GET', `/diff?name=${encodeURIComponent(name)}&old=${old}&new=${new_v}`),
  spec: (name: string, version?: string) =>
    request<SpecResponse>('GET', `/spec/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}`),
  specCompile: (text: string) => request<SpecCompileResponse>('POST', '/spec/compile', { text }),
  faults: () => request<FaultsResponse>('GET', '/faults'),

  // ── v1.1.0 设备目录 / 实体解析（模块 D）──
  catalog: () => request<CatalogSnapshot>('GET', '/catalog'),
  catalogRefresh: (full = true, domain = '', area = '') =>
    request<CatalogRefreshResponse>('POST', '/catalog/refresh', { full, domain, area }),
  catalogAliases: () => request<AliasesResponse>('GET', '/catalog/aliases'),
  catalogSetAlias: (name: string, entity_id: string) =>
    request<AliasesResponse>('POST', '/catalog/alias', { name, entity_id }),
  catalogRemoveAlias: (name: string) =>
    request<AliasesResponse>('POST', '/catalog/alias/remove', { name }),
  catalogResolveMetrics: () => request<ResolveMetricsResponse>('GET', '/catalog/resolve-metrics'),
  entitiesResolve: (name: string, area = '', domain = '', top_n = 8) =>
    request<ResolveResponse>('GET', `/entities/resolve?name=${encodeURIComponent(name)}&area=${encodeURIComponent(area)}&domain=${encodeURIComponent(domain)}&top_n=${top_n}`),
  entities: (domain = '', area = '', keyword = '', limit = 50, offset = 0) =>
    request<EntityListResponse>('GET', `/entities?domain=${encodeURIComponent(domain)}&area=${encodeURIComponent(area)}&keyword=${encodeURIComponent(keyword)}&limit=${limit}&offset=${offset}`),
  entityState: (entity_id: string) =>
    request<EntityStateResponse>('GET', `/entities/${encodeURIComponent(entity_id)}/state`),

  // ── v0.6.0 标签体系 + 批量启停（模块 B 增强）──
  setGraphTags: (name: string, tags: string[]) =>
    request<GraphTagsResponse>('POST', '/graphs/tags', { name, tags }),
  enableByTag: (tag: string, enable: boolean) =>
    request<EnableByTagResponse>('POST', enable ? '/graphs/enable' : '/graphs/disable', { tag }),

  // ── v0.7.0 备份导入/导出（模块 K）──
  storeExport: () => request<StoreExportResponse>('GET', '/store/export'),
  storeImport: (bundle: unknown, strategy = 'skip') =>
    request<StoreImportResponse>('POST', '/store/import', { bundle, strategy }),

  // ── v1.6.0 绑定（模块 C 增强）──
  bind: (ir: unknown) => request<BindResult>('POST', '/bind', { ir }),

  // ── v1.7.0-b 治理与实时（批次 B）──
  // F 待批队列
  pendingList: (agent?: string) =>
    request<PendingListResponse>('POST', '/pending/list', agent ? { agent } : {}),
  pendingApprove: (op_id: string) =>
    request<ApproveResponse>('POST', '/pending/approve', { op_id }),
  pendingReject: (op_id: string, reason = '') =>
    request<RejectResponse>('POST', '/pending/reject', { op_id, reason }),

  // H 凭据
  credentials: () => request<CredentialsResponse>('GET', '/credentials'),
  updateCredentials: (ha_token?: string, api_token?: string) =>
    request<CredentialsUpdateResponse>('POST', '/credentials/update', { ha_token, api_token }),

  // I 令牌
  whoami: () => request<WhoamiResponse>('GET', '/auth/whoami'),
  subjects: () => request<SubjectsResponse>('GET', '/auth/subjects'),
  revokeToken: (token: string) => request<RevokeResponse>('POST', '/auth/revoke', { token }),

  // G 真机下发
  liveStatus: () => request<LiveStatusResponse>('GET', '/live/status'),
  liveRun: (ir: unknown, live_allow: string[], confirm: boolean, events?: unknown[], undo?: boolean) =>
    request<LiveRunResponse>('POST', '/live/run', { ir, live_allow, confirm, events, undo }),

  // F7 撤销：撤销 = 对真实设备再下发一次，闸门与 liveRun 同源（服务端令牌 + live 权限）
  undoAvailable: () => request<UndoAvailableResponse>('GET', '/undo/available'),
  undoPreview: (deploy_id: string) =>
    request<UndoPreviewResponse>('GET', `/undo/${encodeURIComponent(deploy_id)}`),
  undoDeploy: (deploy_id: string, confirm: boolean) =>
    request<UndoRunResponse>('POST', `/undo/${encodeURIComponent(deploy_id)}`, { confirm }),

  // v1.7.3 运行中 watch 实例
  watchList: () => request<WatchListResponse>('GET', '/watch/list'),
  // F4 ③：生产态验证证据三档（verified / failed / unmodeled）
  evidenceProd: () => request<EvidenceProdResponse>('GET', '/evidence/prod'),

  // ── ADM 第 1 步 ④A：MA 洞察提案队列（approve 只交接进待批，**不部署**）──
  insightsPending: (includeDecided = false) =>
    request<InsightsPendingResponse>('GET', `/insights/pending?include_decided=${includeDecided}`),
  insightApprove: (proposal_id: string, reviewer = '') =>
    request<InsightApproveResponse>('POST', '/insights/approve', { proposal_id, reviewer }),
  insightReject: (proposal_id: string, reason = '', reviewer = '') =>
    request<InsightRejectResponse>('POST', '/insights/reject', { proposal_id, reason, reviewer }),
  metrics: () => request<MetricsResponse>('GET', '/metrics'),
  experience: (limit = 20) => request<ExperienceResponse>('GET', `/experience?limit=${limit}`),
  telemetry: (days = 30) => request<TelemetryResponse>('GET', `/telemetry?days=${days}`),

  // ── v2 M3 结构化 Ask（原生控件 + clarify 流程）──
  // 进程内会话的挂起 ask（仿真会话可见），含后端 AskSpec.control() 的控件元数据
  asksPending: () => request<AsksResponse>('GET', '/asks'),
  // watch sidecar（真机 watch 进程写出的挂起 ask），与 /asks 互补
  asksSidecar: () => request<AsksResponse>('GET', '/asks/pending'),
  askAnswer: (payload: { ask_id?: string | null; room?: string | null; text?: string; answer?: Record<string, unknown> }) =>
    request<AskAnswerResponse>('POST', '/asks/answer', payload),
  // 结构化应答走会话端点：可拿到 422（校验被拒，会话保持挂起可重答）
  // 成功时后端回**整个会话快照**（`_session_view`），不是 ack
  sessionAnswer: (sid: string, payload: { ask_id?: string | null; room?: string | null; text?: string; answer?: Record<string, unknown> }) =>
    request<SessionViewResponse>('POST', `/sessions/${encodeURIComponent(sid)}/answer`, payload),
}
