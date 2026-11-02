import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { normalizeL2Summary, toPendingItem } from '../src/logic/pending.ts'

// AF-726 · 交付面（ui-user-mimo）待批列表的 L2 摘要判据。
// 三态必须可区分（家规同管理员面 `ui/PendingView.vue`：Array.isArray ? list : null）：
//   非空数组＝含这些 L2 ／ 空数组＝本条不含 L2 ／ 缺失或坏形状＝未读到（null）
// ⛔ 把缺失归一成空数组，会把「没读出来」说成「没有」＝假保证（PM 复核退回的正是这处）。
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

test('腿②a 反向腿：缺字段/坏形状 ⇒ null（「未读到」，⛔ 不是 []）', () => {
  const missing = toPendingItem(rawRow(), identity)
  assert.equal(missing.l2_summary, null, '缺失必须归一为 null：否则后端旧版时用户会被告知「本条不含 L2」＝假保证')
  for (const bad of [undefined, null, 'not-an-array', 42, {}]) {
    assert.equal(normalizeL2Summary(bad), null, `坏形状 ${String(bad)} 必须归一为 null（未读到）`)
  }
})

test('腿②b 空数组 ⇒ []（本条确实不含 L2）——必须与 ②a 分开断言', () => {
  const empty = toPendingItem(rawRow({ l2_summary: [] }), identity)
  assert.ok(Array.isArray(empty.l2_summary), '空数组必须在位（不是 null）')
  assert.deepEqual(empty.l2_summary, [])
})

test('腿③ 渲染形状：待批卡片逐条渲染 L2 ＋ 空态显式占位 ＋ 未读到态可区分', () => {
  const view = read('src/views/AutomationsView.vue')
  assert.ok(view.includes('v-for="p in store.pendings"'), '待批列表不存在')
  assert.ok(view.includes('p.l2_summary'), '待批卡片没消费 l2_summary')
  assert.ok(view.includes('本条含：'), '缺少「本条含：`action`（alias）」形状')
  assert.ok(view.includes('本条不含 L2 动作'), '空数组缺显式占位（⛔ 留白＝没读出来）')
  assert.ok(view.includes('未读到 L2 摘要'), '缺字段态必须与空数组区分渲染（⛔ 两态不能同句）')
  assert.ok(view.includes('Array.isArray(p.l2_summary)'), '三态分流必须靠 Array.isArray（否则 null 会被当空态）')
  const types = read('src/types/api.ts')
  assert.ok(/interface PendingItem[\s\S]*?l2_summary\?: L2Action\[\] \| null/.test(types), 'PendingItem 的 l2_summary 必须允许 null（未读到）')
})