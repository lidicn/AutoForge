//: 全树只在这里读一次环境变量（原来 `api/index.ts` 与 `stores/main.ts` 各抄一遍）。
//: ① 缺省档按裁定 20261005 §一 Q3 收敛为「缺省 = 真后端，mock 必须显式开」：
//:    判据从 `!== 'false'` 换成 `=== 'true'`，交付构建漏写 env 时不再静默上线 mock。
//: ② `import.meta.env` 只有 Vite 构建期存在，裸 node 跑判据时它是 undefined——原先直接点值
//:    在模块加载期就抛 TypeError。node 档退到 `--env-file` 写进 process.env 的同名键，
//:    两条路都不给值就是缺省档（真后端），所以测试跑法必须显式声明 mock。
const src = (import.meta as unknown as { env?: Record<string, string | undefined> }).env
  ?? (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env
  ?? {}

export const USE_MOCK = src.VITE_USE_MOCK === 'true'
export const API_BASE = src.VITE_API_BASE ?? ''
//: MCP 端点与页面同源：服务端把 `POST /mcp` 挂在同一个服务层上（`af_api.py`），所以缺省取
//:   当前 origin 拼出来。把 LAN 地址抄死在源码里的那枚常数，换一次部署就会显示一个连不上的
//:   URL——而这张卡片显示的正是「Agent 该连哪里」，显示错就等于配对错。
//:   非同源部署（反代、独立端口）用 `VITE_MCP_URL` 显式覆盖。裸 node 跑判据时没有 `location`，
//:   取值由 `tests/mock.env` 当场给；两条路都不给就是空串，不编一枚看起来能用的地址。
export const MCP_URL =
  src.VITE_MCP_URL ||
  (typeof location === 'object' && location.origin ? `${location.origin}/mcp` : '')
