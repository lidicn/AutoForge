import { api as realApi, clearToken, getToken, setToken } from './client'
import type { Api } from './client'
import { mockApi } from './mock'

export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// 前后端统一接口：VITE_USE_MOCK=true 走桩数据，否则走真实后端端点。
// 取的是模块**导出的那个对象**，不是命名空间：`import * as mockApi` 拿到的键是 `mockApi`，
// 命名空间上没有 `getAgents`，`api.getAgents()` 会在运行期拿到 undefined。
// `as Api` 恰好把这件事从编译期抹掉——HEAD 实测去掉 cast 后 vue-tsc 直接报缺 17 个方法。
export const api: Api = USE_MOCK ? mockApi : realApi

export { clearToken, getToken, setToken }
