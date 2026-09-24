import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { Agent, AuthCode, Automation, PairRequest, PendingItem, User } from '../types/api.ts'
import { api } from '../api/index.ts'
import { closePairStream, openPairStream, restoreSession } from '../api/http.ts'
import { activeShortCode, shortRemainMs } from '../logic/authcodes.ts'
import { groupByAgent, splitByArchived } from '../logic/automations.ts'
import { deleteKey, readJSON, writeJSON } from '../logic/storage.ts'

const USE_MOCK = (import.meta.env.VITE_USE_MOCK as string | undefined) !== 'false'
const SESSION_KEY = 'autoforge.session.v1'
const THEME_KEY = 'autoforge.theme.v1'

export const useMainStore = defineStore('main', () => {
  // ---------- state ----------
  const user = ref<User | null>(null)
  const agents = ref<Agent[]>([])
  const automations = ref<Automation[]>([])
  const pendings = ref<PendingItem[]>([])
  const authCodes = ref<AuthCode[]>([])
  const pair = ref<PairRequest | null>(null)
  const now = ref<number>(api.now())
  const loading = ref(false)
  const booted = ref(false)
  const lastError = ref<string | null>(null)
  const darkMode = ref<boolean>(true)   // 默认深色

  // ---------- getters（全部由纯函数派生，不建第二份索引） ----------
  const split = computed(() => splitByArchived(automations.value))
  const enabledGroups = computed(() => groupByAgent(split.value.active))
  const archivedGroups = computed(() => groupByAgent(split.value.archived))
  const archivedCount = computed(() => split.value.archived.length)
  const pendingCount = computed(() => pendings.value.length)
  const longCodes = computed(() =>
    authCodes.value.filter(c => c.type === 'long').slice()
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)))
  const activeShort = computed(() => activeShortCode(authCodes.value, now.value))
  const shortRemain = computed(() => (activeShort.value ? shortRemainMs(activeShort.value, now.value) : 0))

  // ---------- 会话 ----------
  function bootstrap (): void {
    darkMode.value = readJSON<boolean>(THEME_KEY, true)
    applyTheme()
    if (USE_MOCK) user.value = readJSON<User | null>(SESSION_KEY, null)
    booted.value = true
  }

  // 登录态恢复：mock 从会话；真实从 /api/auth/me（带 token）
  async function restore (): Promise<boolean> {
    if (USE_MOCK) {
      if (user.value) { await refresh(); return true }
      return false
    }
    const u = await restoreSession()
    if (u) {
      user.value = u
      openPairStream()
      await refresh()
      return true
    }
    return false
  }

  function applyTheme (): void {
    document.documentElement.setAttribute('data-theme', darkMode.value ? 'dark' : 'light')
  }

  function toggleTheme (): void {
    darkMode.value = !darkMode.value
    writeJSON(THEME_KEY, darkMode.value)
    applyTheme()
  }

  async function login (username: string, password: string): Promise<User> {
    const u = await api.login(username, password)     // fail-closed：失败不写会话
    user.value = u
    writeJSON(SESSION_KEY, u)
    openPairStream()
    await refresh()
    return u
  }

  async function logout (): Promise<void> {
    try { await api.logout() } catch { /* 清理会话 fail-open */ }
    closePairStream()
    user.value = null
    deleteKey(SESSION_KEY)
    agents.value = []
    automations.value = []
    pendings.value = []
    authCodes.value = []
    pair.value = null
    lastError.value = null
  }

  // ---------- 读取（fail-open：失败保留旧数据） ----------
  async function refresh (): Promise<boolean> {
    loading.value = true
    try {
      const [a, b, c, d] = await Promise.allSettled([
        api.listAgents(),
        api.listAutomations(),
        api.listPending(),
        api.listAuthCodes(),
      ])
      let failed = 0
      if (a.status === 'fulfilled') { agents.value = a.value } else { failed++ }
      if (b.status === 'fulfilled') { automations.value = b.value } else { failed++ }
      if (c.status === 'fulfilled') { pendings.value = c.value } else { failed++ }
      if (d.status === 'fulfilled') { authCodes.value = d.value } else { failed++ }
      lastError.value = failed > 0 ? `有 ${failed} 项数据拉取失败，已保留上次结果` : null
      return failed === 0
    } finally {
      loading.value = false
    }
  }

  function tick (): void { now.value = api.now() }

  // ---------- 配对 ----------
  async function startPair (hint: string): Promise<PairRequest> {
    const req = await api.createPairRequest(hint)     // fail-closed
    pair.value = req
    return req
  }

  async function finishPair (): Promise<Agent> {
    const req = pair.value
    if (!req) throw new Error('没有进行中的配对请求')
    const agent = await api.resolvePair(req.code)     // 过期 → PAIR_EXPIRED（fail-closed）
    agents.value = [agent, ...agents.value.filter(a => a.agent_id !== agent.agent_id)]
    pair.value = null
    return agent
  }

  function cancelPair (): void { pair.value = null }

  // ---------- Agent 写操作（乐观更新 + 失败回滚） ----------
  async function renameAgent (agentId: string, name: string): Promise<void> {
    const next = name.trim()
    const prevAgents = agents.value
    const prevAutomations = automations.value
    agents.value = agents.value.map((a): Agent => (a.agent_id === agentId ? { ...a, name: next } : a))
    automations.value = automations.value.map((m): Automation => (m.agent_id === agentId ? { ...m, agent_name: next } : m))
    try {
      await api.renameAgent(agentId, next)
    } catch (e) {
      agents.value = prevAgents
      automations.value = prevAutomations
      throw e
    }
  }

  async function deleteAgent (agentId: string): Promise<void> {
    const prevAgents = agents.value
    agents.value = agents.value.filter(a => a.agent_id !== agentId)
    try {
      await api.deleteAgent(agentId)
    } catch (e) {
      agents.value = prevAgents
      throw e
    }
    // 与后端语义一致：级联归档
    automations.value = automations.value.map((m): Automation => (m.agent_id === agentId ? { ...m, archived: true } : m))
  }

  // ---------- 自动化写操作 ----------
  async function toggleAutomation (item: Automation): Promise<void> {
    const enabled = item.status !== 'enabled'
    const prev = automations.value
    automations.value = automations.value.map((m): Automation =>
      m.id === item.id ? { ...m, status: enabled ? 'enabled' : 'disabled' } : m)
    try {
      await api.setAutomationEnabled(item.id, enabled)
    } catch (e) {
      automations.value = prev
      throw e
    }
  }

  async function archiveAutomation (item: Automation, archived: boolean): Promise<void> {
    const prev = automations.value
    automations.value = automations.value.map((m): Automation => (m.id === item.id ? { ...m, archived } : m))
    try {
      await api.setAutomationArchived(item.id, archived)
    } catch (e) {
      automations.value = prev
      throw e
    }
  }

  async function deleteAutomation (id: string): Promise<void> {
    const prev = automations.value
    automations.value = automations.value.filter(m => m.id !== id)
    try {
      await api.deleteAutomation(id)
    } catch (e) {
      automations.value = prev
      throw e
    }
  }

  // ---------- 待批 ----------
  async function resolvePending (opId: string, approve: boolean): Promise<void> {
    const prevPendings = pendings.value
    const prevAutomations = automations.value
    if (!pendings.value.some(p => p.op_id === opId)) throw new Error('待批项不存在或已被处理')
    pendings.value = pendings.value.filter(p => p.op_id !== opId)
    automations.value = automations.value.map((m): Automation => {
      if (m.id !== opId) return m
      return approve ? { ...m, status: 'enabled' } : { ...m, archived: true }
    })
    try {
      await api.resolvePending(opId, approve)
    } catch (e) {
      pendings.value = prevPendings
      automations.value = prevAutomations
      throw e
    }
  }

  const approvePending = (opId: string) => resolvePending(opId, true)
  const rejectPending = (opId: string) => resolvePending(opId, false)

  // ---------- 授权码 ----------
  async function createLongCode (): Promise<AuthCode> {
    const c = await api.createLongCode()
    authCodes.value = [c, ...authCodes.value]
    return c
  }

  async function createShortCode (minutes: number): Promise<AuthCode> {
    const c = await api.createShortCode(minutes)      // 越界 / 已有生效短期码 → 抛错
    authCodes.value = [c, ...authCodes.value.filter(x => x.type !== 'short')]
    return c
  }

  async function revokeCode (code: string): Promise<void> {
    const prev = authCodes.value
    authCodes.value = authCodes.value.filter(c => c.code !== code)
    try {
      await api.revokeAuthCode(code)
    } catch (e) {
      authCodes.value = prev
      throw e
    }
  }

  return {
    user, agents, automations, pendings, authCodes, pair, now, loading, booted, lastError, darkMode,
    split, enabledGroups, archivedGroups, archivedCount, pendingCount, longCodes, activeShort, shortRemain,
    bootstrap, login, logout, refresh, tick, toggleTheme, restore,
    startPair, finishPair, cancelPair,
    renameAgent, deleteAgent,
    toggleAutomation, archiveAutomation, deleteAutomation,
    resolvePending, approvePending, rejectPending,
    createLongCode, createShortCode, revokeCode,
  }
})