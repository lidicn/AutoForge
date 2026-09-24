import test from 'node:test'
import assert from 'node:assert/strict'
import {
  SHORT_MAX_MINUTES,
  SHORT_MIN_MINUTES,
  activeShortCode,
  clampShortMinutes,
  codeKindMeta,
  shortRemainMs,
  validateShortMinutes,
} from '../src/logic/authcodes.ts'

const NOW = Date.parse('2026-09-24T12:00:00.000Z')

test('滑块 5–30 分钟：边界内通过', () => {
  assert.equal(SHORT_MIN_MINUTES, 5)
  assert.equal(SHORT_MAX_MINUTES, 30)
  assert.equal(validateShortMinutes(5).ok, true)
  assert.equal(validateShortMinutes(10).ok, true)
  assert.equal(validateShortMinutes(30).ok, true)
})

test('滑块边界外 / 非整数 fail-closed 拒绝，不静默 clamp', () => {
  assert.equal(validateShortMinutes(4).ok, false)
  assert.match(validateShortMinutes(4).message, /最短/)
  assert.equal(validateShortMinutes(31).ok, false)
  assert.match(validateShortMinutes(31).message, /最长/)
  assert.equal(validateShortMinutes(10.5).ok, false)
  assert.equal(validateShortMinutes(NaN).ok, false)
  assert.equal(validateShortMinutes(Infinity).ok, false)
})

test('clampShortMinutes 仅用于游标兜底，范围锁死在 [5, 30]', () => {
  assert.equal(clampShortMinutes(1), 5)
  assert.equal(clampShortMinutes(99), 30)
  assert.equal(clampShortMinutes(10), 10)
  assert.equal(clampShortMinutes(NaN), 5)
})

test('shortRemainMs 边界：到期当刻归零，长期码恒为 0', () => {
  const code = { code: '123456', type: 'short', created_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + 60_000).toISOString() }
  assert.equal(shortRemainMs(code, NOW), 60_000)
  assert.equal(shortRemainMs(code, NOW + 60_000), 0)
  assert.equal(shortRemainMs({ ...code, type: 'long' }, NOW), 0)
  assert.equal(shortRemainMs({ ...code, expires_at: 'bad' }, NOW), 0)
})

test('activeShortCode 同时至多一个生效短期码', () => {
  const live = { code: '111111', type: 'short', created_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + 60_000).toISOString() }
  const dead = { code: '222222', type: 'short', created_at: new Date(NOW - 600_000).toISOString(), expires_at: new Date(NOW - 1_000).toISOString() }
  const long = { code: 'AF-AAAA-BBBB-CCCC', type: 'long', created_at: new Date(NOW).toISOString(), expires_at: null }

  assert.equal(activeShortCode([long], NOW), null)             // 只有长期码
  assert.equal(activeShortCode([dead, long], NOW), null)        // 只有过期短期码
  assert.equal(activeShortCode([live, dead], NOW).code, '111111')
})

test('codeKindMeta 长短期标签', () => {
  assert.equal(codeKindMeta('short').label, '短期')
  assert.equal(codeKindMeta('long').label, '长期')
  assert.equal(codeKindMeta('weird').label, '长期')             // 兜底按长期展示
})