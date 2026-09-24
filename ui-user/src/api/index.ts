import * as mockApi from './mock'
import * as realApi from './client'
import type { Api } from './client'

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// 前后端统一接口：VITE_USE_MOCK=true 走桩数据，否则走真实后端端点。
export const api: Api = (USE_MOCK ? mockApi : realApi) as Api

export const getToken = realApi.getToken
export const setToken = realApi.setToken
export const clearToken = realApi.clearToken
