/**
 * AutoForge 用户端 · API 客户端出口
 * 按 VITE_USE_MOCK 在 mock 与真实实现间切换（缺省 = 真后端，mock 必须显式开 = 'true'）。
 */
import { mockApi } from './mock.ts'
import { httpApi } from './http.ts'
import { USE_MOCK } from './env.ts'

export const api = USE_MOCK ? mockApi : httpApi

export { MCP_URL, apiError } from './mock.ts'
export type { ApiClient, ApiErrorCode } from './mock.ts'
