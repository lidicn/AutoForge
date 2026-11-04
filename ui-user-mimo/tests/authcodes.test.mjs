import test from 'node:test'
import assert from 'node:assert/strict'
import {
  CODE_STATUS_LABEL,
  SHORT_MAX_MINUTES,
  SHORT_MIN_MINUTES,
  activeShortCode,
  clampShortMinutes,
  codeKindMeta,
  codeStatus,
  latestShortCode,
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

test('🔴 已消耗的短期码不算「生效中」（倒计时必须停）——2026-11-04 出资人缺陷', () => {
  // 病灶：码被 consume 后 expires_at 还没到，旧判定只看时间 ⇒ 界面继续倒计时、还能复制/作废。
  const consumed = { code: '333333', type: 'short', created_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + 600_000).toISOString(), consumed: true }
  assert.equal(shortRemainMs(consumed, NOW) > 0, true)      // 时间上还没过期 ⇒ 复现前提成立
  assert.equal(activeShortCode([consumed], NOW), null)      // ⛔ 但不能算生效中
  assert.equal(codeStatus(consumed, NOW), 'consumed')
})

test('已作废 / 已过期的短期码同样不算生效中；consumed 优先于 revoked', () => {
  const revoked = { code: '444444', type: 'short', created_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + 600_000).toISOString(), revoked: true }
  const expired = { code: '555555', type: 'short', created_at: new Date(NOW - 600_000).toISOString(), expires_at: new Date(NOW - 1).toISOString() }
  assert.equal(activeShortCode([revoked], NOW), null)
  assert.equal(codeStatus(revoked, NOW), 'revoked')
  assert.equal(activeShortCode([expired], NOW), null)
  assert.equal(codeStatus(expired, NOW), 'expired')
  // 后端历史数据里 consumed+revoked 同为 true 的码按「已消耗」展示（先消耗、后作废）
  assert.equal(codeStatus({ ...revoked, consumed: true }, NOW), 'consumed')
})

test('后端已给 status 时优先采信（⛔ 不另造第二口径）；唯一例外是 active→本地到期降级', () => {
  const base = { code: '888888', type: 'short', created_at: new Date(NOW - 60_000).toISOString(), expires_at: new Date(NOW + 600_000).toISOString() }
  assert.equal(codeStatus({ ...base, status: 'consumed' }, NOW), 'consumed')
  assert.equal(codeStatus({ ...base, status: 'revoked' }, NOW), 'revoked')
  assert.equal(codeStatus({ ...base, status: 'active' }, NOW), 'active')
  // 页面开着、本地钟越过过期点：即便后端上次抓取还说 active，也要降级成 expired（不能停在 00:00）
  assert.equal(codeStatus({ ...base, status: 'active' }, NOW + 700_000), 'expired')
  // 旧后端（无 status）走兜底：consumed 优先于 revoked
  const legacy = { ...base, consumed: true, revoked: true }
  assert.equal(codeStatus(legacy, NOW), 'consumed')
})

test('latestShortCode 取最近一枚短期码（含已消耗），长期码不参与；标签齐全', () => {
  const older = { code: '666666', type: 'short', created_at: new Date(NOW - 900_000).toISOString(), expires_at: new Date(NOW - 300_000).toISOString(), consumed: true }
  const newer = { code: '777777', type: 'short', created_at: new Date(NOW - 60_000).toISOString(), expires_at: new Date(NOW + 240_000).toISOString() }
  const long = { code: 'AF-AAAA', type: 'long', created_at: new Date(NOW).toISOString(), expires_at: null }
  assert.equal(latestShortCode([older, long])?.code, '666666')
  assert.equal(latestShortCode([older, newer, long])?.code, '777777')
  assert.equal(latestShortCode([long]), null)
  assert.deepEqual(
    [CODE_STATUS_LABEL.active, CODE_STATUS_LABEL.consumed, CODE_STATUS_LABEL.revoked, CODE_STATUS_LABEL.expired],
    ['生效中', '已消耗', '已作废', '已过期'],
  )
})