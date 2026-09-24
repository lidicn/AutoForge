import test from 'node:test'
import assert from 'node:assert/strict'
import { errorMessage, formatAgo, formatCountdown, formatDateTime, pad2 } from '../src/logic/format.ts'

test('pad2 两位补零，且不截断两位以上', () => {
  assert.equal(pad2(0), '00')
  assert.equal(pad2(9), '09')
  assert.equal(pad2(59), '59')
  assert.equal(pad2(125), '125')
})

test('formatCountdown 输出 MM:SS，不足 1s 向上取整', () => {
  assert.equal(formatCountdown(0), '00:00')
  assert.equal(formatCountdown(1), '00:01')            // 1ms 也不许显示 00:00
  assert.equal(formatCountdown(999), '00:01')
  assert.equal(formatCountdown(59_000), '00:59')
  assert.equal(formatCountdown(60_000), '01:00')
  assert.equal(formatCountdown(5 * 60_000 + 3_000), '05:03')
  assert.equal(formatCountdown(30 * 60_000), '30:00')  // 滑块上限
})

test('formatCountdown 边界：负数归零，超过 99 分钟不截断', () => {
  assert.equal(formatCountdown(-1), '00:00')
  assert.equal(formatCountdown(-60_000), '00:00')
  assert.equal(formatCountdown(125 * 60_000), '125:00')
})

test('formatAgo 相对时间分档（含边界）', () => {
  const now = Date.parse('2026-09-24T12:00:00.000Z')
  assert.equal(formatAgo(null, now), '从未触发')
  assert.equal(formatAgo('not-a-date', now), '从未触发')
  assert.equal(formatAgo(new Date(now - 1_000).toISOString(), now), '刚刚')
  assert.equal(formatAgo(new Date(now - 59_000).toISOString(), now), '刚刚')
  assert.equal(formatAgo(new Date(now - 60_000).toISOString(), now), '1 分钟前')
  assert.equal(formatAgo(new Date(now - 59 * 60_000).toISOString(), now), '59 分钟前')
  assert.equal(formatAgo(new Date(now - 3_600_000).toISOString(), now), '1 小时前')
  assert.equal(formatAgo(new Date(now - 23 * 3_600_000).toISOString(), now), '23 小时前')
  assert.equal(formatAgo(new Date(now - 25 * 3_600_000).toISOString(), now), '1 天前')
  assert.equal(formatAgo(new Date(now - 30 * 24 * 3_600_000).toISOString(), now), '30 天前')
})

test('formatAgo 异常路径：未来时间（时钟漂移）不显示负数，超 30 天回落绝对时间', () => {
  const now = Date.parse('2026-09-24T12:00:00.000Z')
  assert.equal(formatAgo(new Date(now + 600_000).toISOString(), now), '刚刚')
  const local = new Date(2026, 0, 5, 8, 3)
  assert.equal(formatAgo(local.toISOString(), now), '01-05 08:03')
})

test('formatDateTime 边界：null 与非法字符串', () => {
  assert.equal(formatDateTime(null), '—')
  assert.equal(formatDateTime('bad'), '—')
  const local = new Date(2026, 11, 31, 23, 5)
  assert.equal(formatDateTime(local.toISOString()), '12-31 23:05')
})

test('errorMessage 透传 Error/字符串，其余回落', () => {
  assert.equal(errorMessage(new Error('用户名或密码错误')), '用户名或密码错误')
  assert.equal(errorMessage('网络抖动'), '网络抖动')
  assert.equal(errorMessage(null), '操作失败')
  assert.equal(errorMessage(undefined, '自定义'), '自定义')
})