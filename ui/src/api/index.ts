import { api } from '../api/client'
import { mockApi } from '../api/mock'

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const facade = {
  health: () => USE_MOCK ? mockApi.health() : api.health(),
  graphs: () => USE_MOCK ? mockApi.graphs() : api.graphs(),
  graph: (name: string, version?: string) => USE_MOCK ? mockApi.graph(name) : api.graph(name, version),
  build: (ir: unknown) => USE_MOCK ? mockApi.build(ir) : api.build(ir),
  sim: (ir: unknown, seed?: Record<string, string>, events?: unknown[]) => USE_MOCK ? mockApi.sim(ir, seed, events) : api.sim(ir, seed, events),
  conf: (name: string) => USE_MOCK ? mockApi.conf(name) : api.conf(name),
  intervene: (name: string, automation_id: string) => USE_MOCK ? mockApi.intervene(name, automation_id) : api.intervene(name, automation_id),
  diff: (name: string, old: string, new_v: string) => USE_MOCK ? mockApi.diff(name, old, new_v) : api.diff(name, old, new_v),
  spec: (name: string, version?: string) => USE_MOCK ? mockApi.spec(name) : api.spec(name, version),
  specCompile: (text: string) => USE_MOCK ? mockApi.specCompile(text) : api.specCompile(text),
  faults: () => USE_MOCK ? mockApi.faults() : api.faults(),

  // ── 批次 A 新增端点：直接走真实 API（生产 VITE_USE_MOCK=false；无对应 mock 夹具）──
  catalog: () => api.catalog(),
  catalogRefresh: (full?: boolean, domain?: string, area?: string) => api.catalogRefresh(full, domain, area),
  catalogAliases: () => api.catalogAliases(),
  catalogSetAlias: (name: string, entity_id: string) => api.catalogSetAlias(name, entity_id),
  catalogRemoveAlias: (name: string) => api.catalogRemoveAlias(name),
  catalogResolveMetrics: () => api.catalogResolveMetrics(),
  entitiesResolve: (name: string, area?: string, domain?: string, top_n?: number) =>
    api.entitiesResolve(name, area, domain, top_n),
  entities: (domain?: string, area?: string, keyword?: string, limit?: number, offset?: number) =>
    api.entities(domain, area, keyword, limit, offset),
  entityState: (entity_id: string) => api.entityState(entity_id),
  setGraphTags: (name: string, tags: string[]) => api.setGraphTags(name, tags),
  enableByTag: (tag: string, enable: boolean) => api.enableByTag(tag, enable),
  storeExport: () => api.storeExport(),
  storeImport: (bundle: unknown, strategy?: string) => api.storeImport(bundle, strategy),
  bind: (ir: unknown) => api.bind(ir),

  // ── v1.7.0-b 治理与实时（批次 B）──
  pendingList: (agent?: string) => api.pendingList(agent),
  pendingApprove: (op_id: string) => api.pendingApprove(op_id),
  pendingReject: (op_id: string, reason?: string) => api.pendingReject(op_id, reason),
  credentials: () => api.credentials(),
  updateCredentials: (ha_token?: string, api_token?: string) => api.updateCredentials(ha_token, api_token),
  whoami: () => api.whoami(),
  subjects: () => api.subjects(),
  revokeToken: (token: string) => api.revokeToken(token),
  liveStatus: () => api.liveStatus(),
  liveRun: (ir: unknown, live_allow: string[], confirm: boolean, events?: unknown[]) =>
    api.liveRun(ir, live_allow, confirm, events),
}
