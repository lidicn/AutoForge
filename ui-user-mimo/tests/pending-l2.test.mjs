import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { normalizeL2Summary, toPendingItem } from '../src/logic/pending.ts'

// AF-726 · 交付面（ui-user-mimo）待批列表的 L2 摘要判据。
// 三条腿：①数据源唯一（逐字透传）②反向腿（后端不给 ⇒ []，⛔ undefined）③渲染形状＋空态。
const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8')
const identity = (v) => (v == null ? '' : String(v))
const rawRow = (over = {}) => ({
  op_id: 'op_1',
  summary: '开空调',
  agent: '客厅主控',
  submitted_at: '2026-11-02T00:00:00Z',
  blast_radius: { affected: 1, limit: 5 },
  ...over,
})

test('腿① 数据源唯一：后端 l2_summary 逐字透传（⛔ 前端不算 L2、⛔ 不改形）', () => {
  const l2 = [{ action: 'climate.turn_on', alias: '打开空调', automation: '空调', node: 'd1' }]
  const item = toPendingItem(rawRow({ l2_summary: l2 }), identity)
  assert.deepEqual(item.l2_summary, l2)
})

test('腿② 反向腿：后端不带/坏形状 ⇒ 空数组（⛔ 不是 undefined）', () => {
  const item = toPendingItem(rawRow(), identity)
  assert.ok(Array.isArray(item.l2_summary), '必须是数组：undefined 会被渲染成「没读出来」，与「本条不含 L2」混淆')
  assert.deepEqual(item.l2_summary, [])
  for (const bad of [undefined, null, 'not-an-array', 42, {}]) {
    assert.deepEqual(normalizeL2Summary(bad), [], `坏形状 ${String(bad)} 必须归空数组`)
  }
})

test('腿③ 渲染形状：待批卡片逐条渲染 L2 ＋ 空态显式占位（⛔ 留白）', () => {
  const view = read('src/views/AutomationsView.vue')
  assert.ok(view.includes('v-for="p in store.pendings"'), '待批列表不存在')
  assert.ok(view.includes('p.l2_summary'), '待批卡片没消费 l2_summary')
  assert.ok(view.includes('本条含：'), '缺少「本条含：`action`（alias）」形状')
  assert.ok(view.includes('本条不含 L2 动作'), '空数组缺显式占位（⛔ 留白＝没读出来）')
  assert.ok(view.includes('未读到 L2 摘要'), '缺字段必须与空数组区分渲染（⛔ 两态不能同句）')
  const types = read('src/types/api.ts')
  assert.ok(/interface PendingItem[\s\S]*?l2_summary\?: L2Action\[\]/.test(types), 'PendingItem 未加 l2_summary 字段')
})