import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// UI-1（DCD 裁定 20261031·矩阵行 2）：部署审批摘要的 L2 动作清单渲染。
// 判据只钉两件事：①回执字段 l2_summary 是唯一数据源（前端不许自己算）；②空数组显式占位。
const read = (p) => readFileSync(new URL('../' + p, import.meta.url), 'utf8')

test('视图渲染 L2 清单：动作名 + 别名括号形状 + 显式空态', () => {
  const src = read('src/views/AutomationsView.vue')
  assert.ok(src.includes('l2_summary'), '视图必须读回执字段 l2_summary（不许自己判 L2）')
  assert.ok(src.includes('本条含：'), '非空形状缺「本条含：」')
  assert.ok(src.includes('（{{ it.alias }}）'), '别名必须是（…）括号形状')
  assert.ok(src.includes('本条不含 L2 动作'), '空数组必须显式占位，⛔ 留白＝没读出来')
  assert.ok(src.includes('未读到 L2 摘要'), '缺字段必须与空数组区分（后端没给 ≠ 本条无 L2）')
})

test('回执契约：mock approve 回 l2_summary 数组（空数组合法），驳回无该字段', async () => {
  const { mockApi } = await import('../src/api/mock.ts')
  const approved = await mockApi.resolvePending('auto_07', true)
  assert.equal(approved.ok, true)
  assert.ok(Array.isArray(approved.l2_summary), 'approve 回执必须带 l2_summary 数组')
  assert.deepEqual(approved.l2_summary, [], 'mock 无 IR 判定能力 ⇒ 按「本条不含 L2」空数组回')
  const rejected = await mockApi.resolvePending('op_102', false)
  assert.equal(rejected.ok, true)
  assert.equal(rejected.l2_summary, undefined, '驳回回执不带 l2_summary')
})
