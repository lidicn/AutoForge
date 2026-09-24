import test from 'node:test'
import assert from 'node:assert/strict'
import {
  PAIR_CODE_LENGTH,
  PAIR_TTL_MS,
  countdownMs,
  flipDelays,
  isExpired,
  isPairCode,
  randomDigits,
} from '../src/logic/pairing.ts'

test('isPairCode 只接受固定长度纯数字', () => {
  assert.equal(isPairCode('123456'), true)
  assert.equal(isPairCode('000000'), true)
  assert.equal(isPairCode('12345'), false)      // 少一位
  assert.equal(isPairCode('1234567'), false)    // 多一位
  assert.equal(isPairCode('12345a'), false)
  assert.equal(isPairCode(''), false)
  assert.equal(PAIR_CODE_LENGTH, 6)
})

test('randomDigits 长度与字符集正确，随机源可注入', () => {
  assert.equal(randomDigits(6, () => 0), '000000')
  assert.equal(randomDigits(6, () => 0.999), '999999')
  assert.match(randomDigits(6), /^\d{6}$/)
  assert.equal(randomDigits(0).length, 0)
})

test('randomDigits 边界：rng 恰好返回 1.0 时不得出现第 10 个数字', () => {
  assert.equal(randomDigits(3, () => 1), '000')
})

test('flipDelays 逐位翻入的错峰表', () => {
  assert.deepEqual(flipDelays(6), [0, 90, 180, 270, 360, 450])
  assert.deepEqual(flipDelays(3, 100), [0, 100, 200])
  assert.deepEqual(flipDelays(0), [])
  assert.deepEqual(flipDelays(-2), [])
})

test('countdownMs / isExpired 边界：到期当刻即过期', () => {
  const expiresAt = '2026-09-24T12:05:00.000Z'
  const t = Date.parse(expiresAt)
  assert.equal(PAIR_TTL_MS, 300_000)
  assert.equal(countdownMs(expiresAt, t - 1_000), 1_000)
  assert.equal(countdownMs(expiresAt, t), 0)
  assert.equal(countdownMs(expiresAt, t + 1_000), -1_000)
  assert.equal(isExpired(expiresAt, t - 1), false)
  assert.equal(isExpired(expiresAt, t), true)
  assert.equal(isExpired(expiresAt, t + 1), true)
})

test('异常路径：非法 expires_at 视为已过期（fail-closed）', () => {
  assert.equal(countdownMs('bad-date', 0), 0)
  assert.equal(isExpired('bad-date', 0), true)
})