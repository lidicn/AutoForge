import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import {  __advanceClock,
  __break,
  __reset,
  __setLatency,
  mockApi,
} from '../src/api/mock.ts'
import { MCP_URL } from '../src/api/env.ts'

beforeEach(() => {
  __reset()
  __setLatency(0)
})

const isErr = (code) => (e) => { assert.equal(e.code, code); return true }

test('MCP 端点取自 env，源码里没有写死的 LAN 地址（顶栏卡片展示用）', () => {
  // 值来自 tests/mock.env：这一条钉的是「env 读得到」，而不是「源码里那枚常数还在」，
  // 所以搬走定义不会把它假绿；LAN 字面量则由下面那条反向断言拦住。
  assert.equal(MCP_URL, 'http://192.168.2.200:8787/mcp')
  assert.ok(!readFileSync(new URL('../src/api/mock.ts', import.meta.url), 'utf8').includes('192.168.2.200'))
})

test('MCP 端点的三档取值：显式覆盖 > 同源 origin > 空（不编造地址）', () => {
  // 子进程量：env 与 location 都是模块加载期读一次，同进程里改属性只会拿到缓存。
  // 空覆盖那一档拦的是 `??` 与 `||` 的界线——`.env.production` 那种"键在、值留空"的写法
  // 若走 `??` 会把卡片显示成空白，看着像"后端没配"，实际是"配置里那一行是空的"。
  const probe = fileURLToPath(new URL('./fixtures/probe-mcp-url.mjs', import.meta.url))
  const run = (env, origin) =>
    execFileSync(process.execPath, ['--experimental-strip-types', probe, origin], {
      encoding: 'utf8',
      env: { ...process.env, VITE_MCP_URL: env },
    }).trim()
  assert.equal(run('http://192.168.9.9:8787/mcp', 'http://nas.local'), 'http://192.168.9.9:8787/mcp')
  assert.equal(run('', 'http://nas.local'), 'http://nas.local/mcp')
  assert.equal(run('', ''), '')
})

test('登录语义与后端对齐：任意非空凭据 ⇒ 单 owner 会话，用户名原样回显', async () => {
  // 这一条钉的是"mock 不再自备一对通用口令"：以前错口令抛 AUTH_FAILED，后端从来没有那条分支。
  const user = await mockApi.login('whoever', 'any-non-empty')
  assert.deepEqual(user, { username: 'whoever', role: 'admin' })
})

test('登录异常路径：空入参（含只有空格）', async () => {
  await assert.rejects(mockApi.login('', ''), isErr('AUTH_INVALID_INPUT'))
  await assert.rejects(mockApi.login('   ', 'non-empty'), isErr('AUTH_INVALID_INPUT'))
  await assert.rejects(mockApi.login('whoever', '   '), isErr('AUTH_INVALID_INPUT'))
  await assert.rejects(mockApi.login('', 'non-empty'), isErr('AUTH_INVALID_INPUT'))
})

test('listAgents 按最后活跃倒序', async () => {
  const agents = await mockApi.listAgents()
  assert.equal(agents.length, 2)
  assert.equal(agents[0].agent_id, 'agt_living')
  assert.ok(Date.parse(agents[0].last_seen) >= Date.parse(agents[1].last_seen))
})

test('renameAgent 改名并同步其自动化名；异常路径：未知 id / 空名', async () => {
  const a = await mockApi.renameAgent('agt_living', '  客厅中枢  ')
  assert.equal(a.name, '客厅中枢')
  const autos = await mockApi.listAutomations()
  assert.ok(autos.filter(m => m.agent_id === 'agt_living').every(m => m.agent_name === '客厅中枢'))

  await assert.rejects(mockApi.renameAgent('nope', 'x'), isErr('NOT_FOUND'))
  await assert.rejects(mockApi.renameAgent('agt_living', '   '), isErr('INVALID_NAME'))
  await assert.rejects(mockApi.renameAgent('agt_living', 'x'.repeat(25)), isErr('INVALID_NAME'))
})

test('deleteAgent 级联归档其自动化（不物理删）', async () => {
  await mockApi.deleteAgent('agt_living')
  const agents = await mockApi.listAgents()
  assert.equal(agents.length, 1)
  const autos = await mockApi.listAutomations()
  assert.ok(autos.filter(m => m.agent_id === 'agt_living').length > 0)
  assert.ok(autos.filter(m => m.agent_id === 'agt_living').every(m => m.archived))
  await assert.rejects(mockApi.deleteAgent('agt_living'), isErr('NOT_FOUND'))
})

test('createPairRequest 产出 6 位数字码 + 5 分钟有效期 + 名称预填', async () => {
  const req = await mockApi.createPairRequest('  ')
  // t0 必须取在调用之后：mock 的 now() 是真墙钟，调用前取会因 await 让出事件循环而多出几毫秒，
  // 把"有效期不超过 5 分钟"的上界判成竞态红（HEAD 实测 ttl=300002）。取在后面误差只剩一个方向。
  const t0 = mockApi.now()
  assert.match(req.code, /^\d{6}$/)
  assert.equal(req.agent_name_hint, '新 Agent')                       // 空名称兜底
  const ttl = Date.parse(req.expires_at) - t0
  assert.ok(ttl > 4 * 60_000 && ttl <= 5 * 60_000, `ttl=${ttl}`)
})

test('resolvePair 成功后入库并消费掉配对码', async () => {
  const req = await mockApi.createPairRequest('阳台网关')
  const before = (await mockApi.listAgents()).length
  const agent = await mockApi.resolvePair(req.code)
  assert.equal(agent.name, '阳台网关')
  assert.equal((await mockApi.listAgents()).length, before + 1)
  await assert.rejects(mockApi.resolvePair(req.code), isErr('PAIR_INVALID'))   // 已消费
})

test('resolvePair 异常路径：过期 / 格式错误（fail-closed）', async () => {
  const req = await mockApi.createPairRequest('过期机')
  __advanceClock(6 * 60_000)
  await assert.rejects(mockApi.resolvePair(req.code), isErr('PAIR_EXPIRED'))
  await assert.rejects(mockApi.resolvePair('12ab56'), isErr('PAIR_INVALID'))
})

test('listAutomations 含归档与待批草稿，交由客户端分组', async () => {
  const autos = await mockApi.listAutomations()
  assert.equal(autos.length, 7)
  assert.ok(autos.some(m => m.archived))
  assert.ok(autos.some(m => m.status === 'pending'))
})

test('setAutomationEnabled 异常路径：待批草稿禁止直接启停', async () => {
  await assert.rejects(mockApi.setAutomationEnabled('auto_07', true), isErr('INVALID_STATE'))
  const m = await mockApi.setAutomationEnabled('auto_01', false)
  assert.equal(m.status, 'disabled')
  await assert.rejects(mockApi.setAutomationEnabled('nope', true), isErr('NOT_FOUND'))
})

test('resolvePending 批准 → 草稿转正；驳回 → 草稿归档', async () => {
  await mockApi.resolvePending('auto_07', true)
  const autos = await mockApi.listAutomations()
  assert.equal(autos.find(m => m.id === 'auto_07').status, 'enabled')
  assert.equal((await mockApi.listPending()).length, 1)

  await mockApi.resolvePending('op_102', false)                  // 无对应草稿的纯操作项
  assert.equal((await mockApi.listPending()).length, 0)
  await assert.rejects(mockApi.resolvePending('op_102', true), isErr('NOT_FOUND'))
})

test('createShortCode 校验 5–30 分钟，并强制同时只一个', async () => {
  await assert.rejects(mockApi.createShortCode(4), isErr('INVALID_MINUTES'))
  await assert.rejects(mockApi.createShortCode(31), isErr('INVALID_MINUTES'))
  await assert.rejects(mockApi.createShortCode(10.5), isErr('INVALID_MINUTES'))
  await assert.rejects(mockApi.createShortCode(10), isErr('SHORT_CODE_EXISTS'))  // 种子里已有一个生效短期码

  await mockApi.revokeAuthCode('482913')
  const c = await mockApi.createShortCode(5)
  assert.equal(c.type, 'short')
  assert.match(c.code, /^\d{6}$/)
  assert.equal(Date.parse(c.expires_at) - Date.parse(c.created_at), 5 * 60_000)
  await assert.rejects(mockApi.createShortCode(30), isErr('SHORT_CODE_EXISTS'))
})

test('createLongCode 生成 AF-XXXX-XXXX-XXXX 且不过期', async () => {
  const c = await mockApi.createLongCode()
  assert.equal(c.type, 'long')
  assert.match(c.code, /^AF-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$/)
  assert.equal(c.expires_at, null)
})

test('过期短期码读取时被清理（fail-open 清理，不通知）', async () => {
  await mockApi.revokeAuthCode('482913')
  await mockApi.createShortCode(5)
  __advanceClock(6 * 60_000)
  const codes = await mockApi.listAuthCodes()
  assert.equal(codes.filter(c => c.type === 'short').length, 0)
  assert.equal(codes.filter(c => c.type === 'long').length, 2)
})

test('revokeAuthCode 异常路径：未知码', async () => {
  await assert.rejects(mockApi.revokeAuthCode('nope'), isErr('NOT_FOUND'))
})

test('__break 注入网络异常一次，随后自愈', async () => {
  __break('listAgents', 1)
  await assert.rejects(mockApi.listAgents(), isErr('NETWORK_ERROR'))
  const agents = await mockApi.listAgents()
  assert.equal(agents.length, 2)
})