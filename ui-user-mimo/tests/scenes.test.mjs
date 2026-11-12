import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { __reset, __setLatency, mockApi } from '../src/api/mock.ts'

beforeEach(() => {
  __reset()
  __setLatency(0)
})

const isErr = (code) => (e) => { assert.equal(e.code, code); return true }

//: 种子里的生效授权（g-1a2b3c4d5e6f，agent=agt_living）——运行场景要用。
const ACTIVE_GRANT = 'g-1a2b3c4d5e6f'

test('listScenes：列表条目不带走 steps 全文，但给出 step_count', async () => {
  const resp = await mockApi.listScenes()
  assert.equal(resp.scenes.length, 2)
  const first = resp.scenes.find((s) => s.scene_id === 'hui_jia_mo_shi')
  assert.ok(first)
  assert.equal(first.name, '回家模式')
  assert.deepEqual(first.aliases, ['回家', '我回来了'])
  assert.equal(first.step_count, 2)
  assert.equal('steps' in first, false)          // ⛔ 列表不塞 steps 全文
})

test('getScene：取全文含 steps（带 step_id）；未知 id 拒绝', async () => {
  const def = await mockApi.getScene('hui_jia_mo_shi')
  assert.equal(def.steps.length, 2)
  assert.equal(def.steps[0].step_id, 'step_1')
  await assert.rejects(mockApi.getScene('nope'), isErr('NOT_FOUND'))
})

test('saveScene：新建成功并进列表；scene_id 非法/空名/空步骤被拒', async () => {
  const created = await mockApi.saveScene({
    scene_id: 'li_jia_mo_shi',
    name: '离家模式',
    aliases: ['出门', '我走了'],
    condition: null,
    steps: [{ step_id: 'step_1', adapter: 'ha', action: 'light.turn_off', target: 'light.living_main', params: {} }],
  })
  assert.equal(created.version, 1)
  assert.equal((await mockApi.listScenes()).scenes.length, 3)

  await assert.rejects(mockApi.saveScene({ scene_id: 'Bad-Id', name: 'x', steps: [{ action: 'a', target: 'b' }] }), isErr('BAD_REQUEST'))
  await assert.rejects(mockApi.saveScene({ scene_id: 'ok_id', name: '  ', steps: [{ action: 'a', target: 'b' }] }), isErr('BAD_REQUEST'))
  await assert.rejects(mockApi.saveScene({ scene_id: 'ok_id', name: 'x', steps: [] }), isErr('BAD_REQUEST'))
})

test('saveScene：scene_id 撞车默认拒（INVALID_STATE），显式 replace 才覆盖且 version+1', async () => {
  const scene = {
    scene_id: 'hui_jia_mo_shi',
    name: '回家模式改',
    aliases: [],
    condition: null,
    steps: [{ step_id: 'step_1', adapter: 'ha', action: 'light.turn_off', target: 'light.living_main', params: {} }],
  }
  await assert.rejects(mockApi.saveScene(scene, false), isErr('INVALID_STATE'))
  const replaced = await mockApi.saveScene(scene, true)
  assert.equal(replaced.version, 2)
  assert.equal(replaced.name, '回家模式改')
})

test('deleteScene：归档语义（返回 archived_to 指向 .scenes/deleted/），删两次第二次 NOT_FOUND', async () => {
  const r = await mockApi.deleteScene('shui_jiao_mo_shi')
  assert.equal(r.deleted, true)
  assert.match(r.archived_to, /\.scenes\/deleted\//)
  assert.equal((await mockApi.listScenes()).scenes.length, 1)
  await assert.rejects(mockApi.deleteScene('shui_jiao_mo_shi'), isErr('NOT_FOUND'))
})

test('runScene：缺 grant_id / 缺 agent 被拒；正常执行返回逐步回执并计一次 uses', async () => {
  await assert.rejects(
    mockApi.runScene('hui_jia_mo_shi', { agent: 'agt_living', grantId: '' }),
    isErr('INVALID_STATE'),
  )
  await assert.rejects(
    mockApi.runScene('hui_jia_mo_shi', { agent: '', grantId: ACTIVE_GRANT }),
    isErr('BAD_REQUEST'),
  )

  const before = (await mockApi.listGrants()).items.find((g) => g.grant_id === ACTIVE_GRANT).uses
  const r = await mockApi.runScene('hui_jia_mo_shi', {
    phrase: '回家模式', agent: 'agt_living', grantId: ACTIVE_GRANT,
  })
  assert.equal(r.code, 'OK')
  assert.equal(r.steps.length, 2)                 // 场景两步
  const after = (await mockApi.listGrants()).items.find((g) => g.grant_id === ACTIVE_GRANT).uses
  assert.equal(after, before + 1)                 // 每 run 计一次
})

test('runScene：未知场景 NOT_FOUND；已撤销授权 INVALID_STATE', async () => {
  await assert.rejects(
    mockApi.runScene('nope', { agent: 'agt_living', grantId: ACTIVE_GRANT }),
    isErr('NOT_FOUND'),
  )
  // 种子里的撤销授权 g-9f8e7d6c5b4a
  await assert.rejects(
    mockApi.runScene('hui_jia_mo_shi', { agent: 'agt_lab', grantId: 'g-9f8e7d6c5b4a' }),
    isErr('INVALID_STATE'),
  )
})
