import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useMainStore } from '../src/stores/main.ts'
import {
  MOCK_CREDENTIALS,
  __advanceClock,
  __break,
  __reset,
  __setLatency,
} from '../src/api/mock.ts'
import { __setBackend } from '../src/logic/storage.ts'
import { formatCountdown } from '../src/logic/format.ts'

beforeEach(() => {
  __reset()
  __setLatency(0)
  __setBackend(null)
  setActivePinia(createPinia())
})

const isErr = (code) => (e) => { assert.equal(e.code, code); return true }

test('登录成功写会话且可被 bootstrap 恢复；登出清空', async () => {
  const store = useMainStore()
  assert.equal(store.user, null)
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  assert.deepEqual({ ...store.user }, { username: 'demo', role: 'admin' })

  setActivePinia(createPinia())
  const again = useMainStore()
  again.bootstrap()
  assert.equal(again.user?.username, 'demo')          // 会话恢复

  await again.logout()
  setActivePinia(createPinia())
  const third = useMainStore()
  third.bootstrap()
  assert.equal(third.user, null)                      // 登出后不再恢复
})

test('登录 fail-closed：口令错误不写会话', async () => {
  const store = useMainStore()
  await assert.rejects(store.login('demo', 'nope'), isErr('AUTH_FAILED'))
  assert.equal(store.user, null)
  assert.equal(store.agents.length, 0)
})

test('refresh 拉齐四类数据并算出待批角标 / 短期码倒计时', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  assert.equal(store.agents.length, 2)
  assert.equal(store.automations.length, 7)
  assert.equal(store.pendingCount, 2)                  // 待批块 → 红色角标
  assert.equal(store.longCodes.length, 2)
  assert.ok(store.activeShort)
  assert.ok(store.shortRemain > 0)
})

test('倒计时随 tick 推进，格式为 MM:SS', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  const before = store.shortRemain
  const label = formatCountdown(before)
  __advanceClock(60_000)
  store.tick()
  assert.ok(Math.abs(store.shortRemain - (before - 60_000)) < 200, `${before} -> ${store.shortRemain}`)
  assert.notEqual(formatCountdown(store.shortRemain), label)
  assert.match(formatCountdown(store.shortRemain), /^\d{2}:\d{2}$/)
})

test('读取 fail-open：单个列表失败保留旧数据并提示', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  const before = store.agents.length
  __break('listAgents', 1)
  const ok = await store.refresh()
  assert.equal(ok, false)
  assert.equal(store.agents.length, before)            // 旧数据保留
  assert.match(store.lastError, /拉取失败/)
  assert.equal(await store.refresh(), true)            // 自愈后清除提示
  assert.equal(store.lastError, null)
})

test('写操作 fail-closed：改名失败回滚（含自动化名同步回滚）', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  __break('renameAgent', 1)
  await assert.rejects(store.renameAgent('agt_living', '客厅中枢'), isErr('NETWORK_ERROR'))
  assert.equal(store.agents.find(a => a.agent_id === 'agt_living').name, '客厅主控')
  assert.ok(store.automations.filter(m => m.agent_id === 'agt_living').every(m => m.agent_name === '客厅主控'))

  await store.renameAgent('agt_living', '客厅中枢')     // 重试成功
  assert.ok(store.automations.filter(m => m.agent_id === 'agt_living').every(m => m.agent_name === '客厅中枢'))
})

test('删除 Agent → 列表减一，其自动化全部归档', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  await store.deleteAgent('agt_living')
  assert.equal(store.agents.length, 1)
  const owned = store.automations.filter(m => m.agent_id === 'agt_living')
  assert.ok(owned.length > 0 && owned.every(m => m.archived))
  assert.ok(store.archivedCount >= owned.length)
})

test('启用/归档分组：待批草稿不进启用列表，归档项进归档列表', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  const activeIds = store.enabledGroups.flatMap(g => g.items.map(i => i.id))
  const archivedIds = store.archivedGroups.flatMap(g => g.items.map(i => i.id))
  assert.ok(activeIds.includes('auto_01'))
  assert.ok(!activeIds.includes('auto_07'))            // pending 草稿 → 待批块
  assert.ok(!activeIds.includes('auto_06'))            // 已归档
  assert.ok(archivedIds.includes('auto_06'))
  assert.ok(store.enabledGroups.every(g => g.agent_name))
})

test('批准待批项 → 角标减一且草稿转正；未知 op_id 不改状态', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  await store.approvePending('auto_07')
  assert.equal(store.pendingCount, 1)
  assert.equal(store.automations.find(m => m.id === 'auto_07').status, 'enabled')

  const snapshot = store.pendingCount
  await assert.rejects(store.rejectPending('op_ghost'), /待批项不存在/)
  assert.equal(store.pendingCount, snapshot)
})

test('驳回待批项 → 草稿归档留痕', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  await store.rejectPending('auto_07')
  assert.equal(store.pendingCount, 1)
  assert.equal(store.automations.find(m => m.id === 'auto_07').archived, true)
})

test('短期码同时只一个：创建被拒且状态不变', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  const codesBefore = store.authCodes.length
  await assert.rejects(store.createShortCode(10), isErr('SHORT_CODE_EXISTS'))
  await assert.rejects(store.createShortCode(4), isErr('INVALID_MINUTES'))
  assert.equal(store.authCodes.length, codesBefore)

  await store.revokeCode(store.activeShort.code)       // 立即作废后可重建
  assert.equal(store.activeShort, null)
  const c = await store.createShortCode(30)
  assert.equal(Date.parse(c.expires_at) - Date.parse(c.created_at), 30 * 60_000)
})

test('作废长期码 fail-closed：失败回滚', async () => {
  const store = useMainStore()
  await store.login(MOCK_CREDENTIALS.username, MOCK_CREDENTIALS.password)
  const target = store.longCodes[0].code
  __break('revokeAuthCode', 1)
  await assert.rejects(store.revokeCode(target), isErr('NETWORK_ERROR'))
  assert.ok(store.longCodes.some(c => c.code === target))
  await store.revokeCode(target)
  assert.ok(!store.longCodes.some(c => c.code === target))
})