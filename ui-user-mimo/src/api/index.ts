/**
 * AutoForge 用户端 · API 客户端出口
 * 按 VITE_USE_MOCK 在 mock 与真实实现间切换（开发默认 mock，生产 false）。
 */
import { mockApi } from './mock.ts'
import { httpApi } from './http.ts'

const USE_MOCK = (import.meta.env.VITE_USE_MOCK as string | undefined) !== 'false'

export const api = USE_MOCK ? mockApi : httpApi

export { MCP_URL, MOCK_CREDENTIALS, apiError } from './mock.ts'
export type { ApiClient, ApiErrorCode } from './mock.ts'
