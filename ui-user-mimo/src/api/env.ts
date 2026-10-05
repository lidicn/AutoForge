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
