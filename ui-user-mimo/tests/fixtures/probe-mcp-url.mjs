//: `MCP_URL` 的取值档要在**子进程**里量：它读的是模块加载期的 env 与 `location`，
//: 在同一进程里改 `process.env` 再 import 只会拿到第一次的缓存结果。
//: 用法：`node --experimental-strip-types tests/fixtures/probe-mcp-url.mjs [origin]`
const origin = process.argv[2] || ''
if (origin) globalThis.location = { origin }

const { MCP_URL } = await import('../../src/api/env.ts')
console.log(MCP_URL)
