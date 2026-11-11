import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import {
  GRANT_STATE_LABEL,
  grantExpiresText,
  grantRemainDays,
  grantRiskLabel,
  grantScopeText,
  grantState,
  groupOrderReceipts,
  hiddenCountText,
  orderDueText,
  orderOutputText,
  orderStatusLabel,
  orderStatusTone,
  orderStepText,
  orderStepsBrief,
  receiptTypeMeta,
} from '../src/logic/grants.ts'
import { __reset, __setLatency, mockApi } from '../src/api/mock.ts'

beforeEach(() => {
  __reset()
  __setLatency(0)
})

const NOW = Date.UTC(2026, 10, 11, 12, 0, 0)

function grant (over = {}) {
  return {
    grant_id: 'g-test', agent: 'agt_living', rooms: ['study'],
    device_scope: { include: ['light.study_main'] },
    action_risk: 'low', expires_at: Math.floor(NOW / 1000) + 3 * 86400,
    revoke: { revoked: false, reason: '', at: '' },
    issued_at: '2026-11-10T00:00:00Z', issued_by: 'owner', issued_via: 'mimo', ttl_days: 7, uses: 1,
    ...over,
  }
}

// ---------------- 授权状态：撤销优先于到期 ----------------

test('grantState：撤销是终态，优先于到期时间', () => {
  const g = grant({ revoke: { revoked: true, reason: '演示', at: '2026-11-11T10:00:00Z' } })
  assert.equal(grantState(g, NOW), 'revoked')
  assert.equal(GRANT_STATE_LABEL.revoked, '已撤销')
})

test('grantState：到期判定按 epoch 秒，边界当刻即过期', () => {
  const at = Math.floor(NOW / 1000)
  assert.equal(grantState(grant({ expires_at: at }), NOW), 'expired')
  assert.equal(grantState(grant({ expires_at: at + 1 }), NOW), 'active')
})

test('grantState：缺失/非法有效期 → 不当作永久有效', () => {
  assert.equal(grantState(grant({ expires_at: 0 }), NOW), 'expired')
  assert.equal(grantState(grant({ expires_at: NaN }), NOW), 'expired')
  assert.equal(grantExpiresText(grant({ expires_at: 0 })), '未读到有效期')
})

test('grantRemainDays：向上取整，过期归零', () => {
  const at = Math.floor(NOW / 1000)
  assert.equal(grantRemainDays(grant({ expires_at: at + 3600 }), NOW), 1)      // 还剩 1 小时 → 1 天
  assert.equal(grantRemainDays(grant({ expires_at: at + 3 * 86400 }), NOW), 3)
  assert.equal(grantRemainDays(grant({ expires_at: at - 5 }), NOW), 0)
})

// ---------------- 范围 / 风险：原样回落，不自造语义 ----------------

test('grantScopeText：all＝全屋，白名单与排除分列', () => {
  assert.equal(grantScopeText(grant({ rooms: ['all'], device_scope: { include: [] } })), '区域 全屋 · 白名单 0 项')
  assert.equal(
    grantScopeText(grant({ rooms: ['study', 'zhu_wo_shi'], device_scope: { include: ['a', 'b'], exclude: ['x'] } })),
    '区域 study、zhu_wo_shi · 白名单 2 项 · 排除 1 项',
  )
})

test('grantRiskLabel：已知枚举翻中文，未知原样显示（⛔ 不显示「未知」）', () => {
  assert.equal(grantRiskLabel('low'), '低风险')
  assert.equal(grantRiskLabel('L2'), 'L2 需确认')
  assert.equal(grantRiskLabel('new-tier'), 'new-tier')
  assert.equal(grantRiskLabel(''), '—')
})

// ---------------- 任务单 ----------------

test('orderStatusLabel / orderStatusTone：未知状态原样显示', () => {
  assert.equal(orderStatusLabel('pending'), '待执行')
  assert.equal(orderStatusLabel('done'), '已完成')
  assert.equal(orderStatusLabel('weird-state'), 'weird-state')
  assert.equal(orderStatusTone('failed'), 'error')
  assert.equal(orderStatusTone('running'), 'info')
  assert.equal(orderStatusTone('weird-state'), 'default')
})

test('orderStepText / orderStepsBrief：参数只带 entity_id，多数步折叠', () => {
  assert.equal(orderStepText({ adapter: 'ha', action: 'light.turn_on', params: { entity_id: 'light.x' } }), 'ha.light.turn_on · light.x')
  assert.equal(orderStepText({ action: 'switch.turn_off' }), 'ha.switch.turn_off')
  const o = {
    steps: [
      { adapter: 'ha', action: 'light.turn_on', params: { entity_id: 'light.x' } },
      { adapter: 'ha', action: 'climate.set_temperature' },
    ],
  }
  assert.equal(orderStepsBrief(o), '2 步 · 首步 ha.light.turn_on · light.x')
  assert.equal(orderStepsBrief({ steps: [] }), '（未记录步骤）')
})

test('orderDueText / orderOutputText：缺值如实说，不编时刻', () => {
  assert.equal(orderDueText({ due_at: 0 }), '未读到到点时刻')
  assert.match(orderDueText({ due_at: Math.floor(NOW / 1000) }), /^\d{2}-\d{2} \d{2}:\d{2}$/)
  assert.equal(orderOutputText({ output: {} }), '（默认 tts）')
  assert.equal(orderOutputText({ output: { channels: ['tts', 'mimo'] } }), 'tts / mimo')
})

// ---------------- 留痕 ----------------

test('receiptTypeMeta：已知流水翻中文，未知原样（⛔ 不塞进「其它」）', () => {
  assert.equal(receiptTypeMeta('TASK_ORDER_FINISHED').label, '执行完成')
  assert.equal(receiptTypeMeta('GRANT_REVOKED').tone, 'warning')
  assert.equal(receiptTypeMeta('SOMETHING_NEW').label, 'SOMETHING_NEW')
})

test('groupOrderReceipts：按单聚合、组序＝首次出现序、缺单号进「未记录单号」', () => {
  const items = [
    { type: 'TASK_ORDER_CREATED', at: 't3', order_id: 'o-1', agent: 'a', status: 'pending', reason: '', steps: [] },
    { type: 'TASK_ORDER_FINISHED', at: 't2', order_id: 'o-2', agent: 'a', status: 'done', reason: '', steps: [] },
    { type: 'TASK_ORDER_CANCELLED', at: 't1', order_id: 'o-1', agent: 'a', status: 'cancelled', reason: '改主意', steps: [] },
    { type: 'TASK_ORDER_CREATED', at: 't0', order_id: '', agent: 'a', status: 'pending', reason: '', steps: [] },
  ]
  const groups = groupOrderReceipts(items)
  assert.deepEqual(groups.map((g) => g.order_id), ['o-1', 'o-2', '（未记录单号）'])
  assert.equal(groups[0].items.length, 2)      // 同单两条留痕落在同一组
})

test('hiddenCountText：0/缺失不显示，>0 才提示（⛔ owner 面常驻噪音）', () => {
  assert.equal(hiddenCountText(undefined), null)
  assert.equal(hiddenCountText(0), null)
  assert.equal(hiddenCountText(4), '另有 4 条不在你名下')
})

// ---------------- mock 行为：终态语义 ----------------

test('mock：授权三态齐全，撤销后立即变已撤销并写留痕', async () => {
  const before = await mockApi.listGrants()
  assert.equal(before.count, 3)
  const states = before.items.map((g) => grantState(g, Date.now()))
  assert.deepEqual([...states].sort(), ['active', 'expired', 'revoked'])
  const active = before.items.find((g) => !g.revoke.revoked && g.expires_at * 1000 > Date.now())
  await mockApi.revokeGrant(active.grant_id, '测试撤销')
  const after = await mockApi.listGrants()
  const got = after.items.find((g) => g.grant_id === active.grant_id)
  assert.equal(got.revoke.revoked, true)
  assert.equal(got.revoke.reason, '测试撤销')
  const receipts = await mockApi.listGrantReceipts(20)
  assert.equal(receipts.items[0].type, 'GRANT_REVOKED')
})

test('mock：重复撤销被拒（INVALID_STATE），⛔ 不静默成功', async () => {
  const list = await mockApi.listGrants()
  const active = list.items.find((g) => !g.revoke.revoked && g.expires_at * 1000 > Date.now())
  await mockApi.revokeGrant(active.grant_id, '')
  await assert.rejects(
    () => mockApi.revokeGrant(active.grant_id, ''),
    (e) => e.code === 'INVALID_STATE',
  )
})

test('mock：取消任务单＝终态即销（活动池消失、留痕多一条）', async () => {
  const before = await mockApi.listOrders()
  assert.equal(before.count, 2)
  const target = before.items[0].order_id
  await mockApi.cancelOrder(target, '测试取消')
  const after = await mockApi.listOrders()
  assert.equal(after.items.length, 1)
  assert.ok(!after.items.some((o) => o.order_id === target))
  const receipts = await mockApi.listOrderReceipts(20)
  assert.equal(receipts.items[0].type, 'TASK_ORDER_CANCELLED')
  assert.equal(receipts.items[0].order_id, target)
  assert.equal(receipts.items[0].reason, '测试取消')
})

test('mock：取消不存在的单被拒（NOT_FOUND）', async () => {
  await assert.rejects(
    () => mockApi.cancelOrder('o-nope', ''),
    (e) => e.code === 'NOT_FOUND',
  )
})

test('mock：留痕 limit 生效（页面只取 20 条，⛔ 全量拉取）', async () => {
  const got = await mockApi.listGrantReceipts(1)
  assert.equal(got.items.length, 1)
})
