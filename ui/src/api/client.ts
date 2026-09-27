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
  AsksResponse, AskAnswerResponse,
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
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  // 真后端返回**扁平**对象（无 { data } 外壳）；此处统一包装成 { data }，
  // 与 mock 层保持一致，视图侧无需改动。
  const json = (await res.json()) as T
  return { data: json }
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
  liveRun: (ir: unknown, live_allow: string[], confirm: boolean, events?: unknown[]) =>
    request<LiveRunResponse>('POST', '/live/run', { ir, live_allow, confirm, events }),

  // v1.7.3 运行中 watch 实例
  watchList: () => request<WatchListResponse>('GET', '/watch/list'),
  metrics: () => request<any>('GET', '/metrics'),
  experience: (limit = 20) => request<any>('GET', `/experience?limit=${limit}`),
  telemetry: (days = 30) => request<any>('GET', `/telemetry?days=${days}`),

  // ── v2 M3 结构化 Ask（原生控件 + clarify 流程）──
  // 进程内会话的挂起 ask（仿真会话可见），含后端 AskSpec.control() 的控件元数据
  asksPending: () => request<AsksResponse>('GET', '/asks'),
  // watch sidecar（真机 watch 进程写出的挂起 ask），与 /asks 互补
  asksSidecar: () => request<AsksResponse>('GET', '/asks/pending'),
  askAnswer: (payload: { ask_id?: string | null; room?: string | null; text?: string; answer?: Record<string, unknown> }) =>
    request<AskAnswerResponse>('POST', '/asks/answer', payload),
  // 结构化应答走会话端点：可拿到 422（校验被拒，会话保持挂起可重答）
  sessionAnswer: (sid: string, payload: { ask_id?: string | null; room?: string | null; text?: string; answer?: Record<string, unknown> }) =>
    request<any>('POST', `/sessions/${sid}/answer`, payload),
}
