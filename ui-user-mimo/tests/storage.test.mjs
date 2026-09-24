import { beforeEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { __setBackend, deleteKey, readJSON, writeJSON } from '../src/logic/storage.ts'

beforeEach(() => { __setBackend(null) })

test('内存兜底下的读写删除闭环', () => {
  writeJSON('k', { a: 1 })
  assert.deepEqual(readJSON('k', null), { a: 1 })
  deleteKey('k')
  assert.deepEqual(readJSON('k', { d: true }), { d: true })
})

test('异常路径：key 不存在 / 内容损坏时回落默认值（fail-open）', () => {
  assert.equal(readJSON('missing', 42), 42)
  __setBackend({ getItem: () => '{bad json', setItem: () => {}, removeItem: () => {} })
  assert.equal(readJSON('k', 'fallback'), 'fallback')
})

test('异常路径：后端抛错不冒泡（隐私模式）', () => {
  const boom = {
    getItem: () => { throw new Error('denied') },
    setItem: () => { throw new Error('denied') },
    removeItem: () => { throw new Error('denied') },
  }
  __setBackend(boom)
  assert.equal(readJSON('k', 'safe'), 'safe')
  assert.doesNotThrow(() => writeJSON('k', 1))
  assert.doesNotThrow(() => deleteKey('k'))
})