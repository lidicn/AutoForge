import test from 'node:test'
import assert from 'node:assert/strict'
import {
  TRIAL_STEPS,
  blastMeta,
  compareAutomation,
  deviceLabel,
  groupByAgent,
  splitByArchived,
  statusMeta,
  trialMeta,
} from '../src/logic/automations.ts'

function mk (over = {}) {
  return {
    id: 'auto_x', name: '示例', agent_id: 'agt_a', agent_name: 'Agent-A', status: 'enabled',
    preview_nl: '示例预演', devices: [], last_triggered: null, trigger_7d: 0,
    archived: false, trial: null,
    ...over,
  }
}

test('statusMeta 四种状态各有标签与配色', () => {
  assert.deepEqual(statusMeta('pending'),  { label: '待批',   tone: 'warning', tagType: 'warning' })
  assert.deepEqual(statusMeta('enabled'),  { label: '已启用', tone: 'success', tagType: 'success' })
  assert.deepEqual(statusMeta('disabled'), { label: '已停用', tone: 'neutral', tagType: 'default' })
  assert.deepEqual(statusMeta('anomaly'),  { label: '异常',   tone: 'error',   tagType: 'error' })
})

test('statusMeta 异常路径：后端新增未知状态走中性兜底（fail-open）', () => {
  const m = statusMeta('quarantined')
  assert.equal(m.label, 'quarantined')
  assert.equal(m.tone, 'neutral')
  assert.equal(m.tagType, 'default')
})

test('splitByArchived：归档归档，pending 草稿交给待批块', () => {
  const { active, archived } = splitByArchived([
    mk({ id: 'a1' }),
    mk({ id: 'a2', status: 'pending' }),
    mk({ id: 'a3', archived: true }),
  ])
  assert.deepEqual(active.map(x => x.id), ['a1'])
  assert.deepEqual(archived.map(x => x.id), ['a3'])
})

test('groupByAgent 按 agent_name 排序、组内按最近触发倒序', () => {
  const groups = groupByAgent([
    mk({ id: 'x2', agent_id: 'agt_b', agent_name: 'Agent-B', name: 'BBB', last_triggered: '2026-09-24T10:00:00.000Z' }),
    mk({ id: 'x1', agent_id: 'agt_a', agent_name: 'Agent-A', name: 'AAA', last_triggered: '2026-09-24T11:00:00.000Z' }),
    mk({ id: 'x3', agent_id: 'agt_a', agent_name: 'Agent-A', name: 'CCC', last_triggered: '2026-09-24T09:00:00.000Z' }),
  ])
  assert.deepEqual(groups.map(g => g.agent_id), ['agt_a', 'agt_b'])
  assert.deepEqual(groups[0].items.map(i => i.id), ['x1', 'x3'])
  assert.equal(groups[0].items.length, 2)
  assert.equal(groups[1].items.length, 1)
})

test('compareAutomation 边界：从未触发排最后，同刻按名称稳定排序', () => {
  const items = [
    mk({ id: 'n', name: '新来的', last_triggered: null }),
    mk({ id: 'b', name: '乙', last_triggered: '2026-09-24T10:00:00.000Z' }),
    mk({ id: 'a', name: '甲', last_triggered: '2026-09-24T10:00:00.000Z' }),
    mk({ id: 'bad', name: '坏时间', last_triggered: 'not-a-date' }),
  ]
  const sorted = items.slice().sort(compareAutomation)
  assert.deepEqual(sorted.map(i => i.id), ['a', 'b', 'n', 'bad'])
})

test('trialMeta 三段放权顺序与当前位置', () => {
  assert.deepEqual(TRIAL_STEPS.map(s => s.key), ['shadow', 'canary', 'auto'])
  assert.equal(trialMeta({ state: 'shadow', since: 'x', anomaly: null }).activeIndex, 0)
  assert.equal(trialMeta({ state: 'canary', since: 'x', anomaly: null }).activeIndex, 1)
  assert.equal(trialMeta({ state: 'auto', since: 'x', anomaly: null }).activeIndex, 2)
})

test('trialMeta 异常路径：无试演记录 / 未知状态 / 有 anomaly', () => {
  const none = trialMeta(null)
  assert.equal(none.available, false)
  assert.equal(none.activeIndex, -1)

  const unknown = trialMeta({ state: 'beta', since: 'x', anomaly: null })
  assert.equal(unknown.activeIndex, -1)

  const bad = trialMeta({ state: 'canary', since: 'x', anomaly: '4 分钟内重复上锁 7 次' })
  assert.equal(bad.level, 'anomaly')
  assert.equal(bad.anomaly, '4 分钟内重复上锁 7 次')
})

test('blastMeta 关键词映射到红色分级角标', () => {
  assert.equal(blastMeta('low').level, 'low')
  assert.equal(blastMeta('medium').level, 'medium')
  assert.equal(blastMeta('high').level, 'high')
  assert.equal(blastMeta('critical').level, 'critical')
  assert.equal(blastMeta('中（涉及 2 个设备）').level, 'medium')
  assert.equal(blastMeta('严重：整层断电').level, 'critical')
})

test('blastMeta 异常路径：自由文本兜底且不吞原文', () => {
  const m = blastMeta('影响客厅一整层')
  assert.equal(m.level, 'unknown')
  assert.equal(m.raw, '影响客厅一整层')
  assert.equal(blastMeta('').level, 'unknown')
  assert.equal(blastMeta('   ').raw, '')
})

test('deviceLabel 优先友好名，缺失回落 entity_id', () => {
  assert.equal(deviceLabel({ friendly_name: '客厅主灯', entity_id: 'light.x' }), '客厅主灯')
  assert.equal(deviceLabel({ friendly_name: '', entity_id: 'light.x' }), 'light.x')
  assert.equal(deviceLabel({ friendly_name: '', entity_id: '' }), '未知设备')
})