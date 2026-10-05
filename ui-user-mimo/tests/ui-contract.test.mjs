import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (p) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const has = (file, needle) => assert.ok(read(file).includes(needle), `${file} 缺少「${needle}」`)

test('§8-4 依赖白名单：无 Element Plus / Ant Design / Tailwind，运行时依赖严格为 5 个', () => {
  const pkg = JSON.parse(read('package.json'))
  assert.deepEqual(Object.keys(pkg.dependencies).sort(), ['@vueuse/core', 'naive-ui', 'pinia', 'vue', 'vue-router'])
  for (const banned of ['element-plus', 'ant-design-vue', 'ant-design', 'tailwindcss']) {
    assert.equal(pkg.dependencies[banned], undefined)
    assert.equal(pkg.devDependencies[banned], undefined)
  }
})

test('§1 契约文件结构未被改动（8 个导出 + 关键联合类型逐字在位）', () => {
  const src = read('src/types/api.ts')
  for (const name of ['User', 'Agent', 'PairRequest', 'AutomationStatus', 'TrialState', 'DeviceRef', 'TrialInfo', 'Automation', 'AuthCodeType', 'AuthCode', 'PendingItem']) {
    assert.ok(src.includes(`export ${src.includes(`interface ${name}`) ? 'interface' : 'type'} ${name}`), `缺少导出 ${name}`)
  }
  assert.ok(src.includes(`role: 'user' | 'admin'`))
  assert.ok(src.includes(`= 'pending' | 'enabled' | 'disabled' | 'anomaly'`))
  assert.ok(src.includes(`= 'auto' | 'shadow' | 'canary'`))
  assert.ok(src.includes(`= 'long' | 'short'`))
  assert.ok(!src.includes('export default'))
})

test('顶栏标题 AutoForge + 底部小字 ForgeSight + 三 Tab + 600px 居中', () => {
  has('src/views/MainLayout.vue', 'AutoForge')
  has('src/views/MainLayout.vue', 'ForgeSight')
  has('src/views/MainLayout.vue', "'Agent'")
  has('src/views/MainLayout.vue', '自动化')
  has('src/views/MainLayout.vue', '授权码')
  has('src/views/MainLayout.vue', '600px')
})

test('Agent Tab：MCP 卡片 + 复制按钮 + 删除配对 + 内联改名', () => {
  // 端点值只在 mock-api.test.mjs 钉一次；这里钉"导出在位 + 视图真引用它"这两个接合点，
  // 同一个字面量抄进两份判据就是下一个改端口的人只改对一处。
  has('src/api/mock.ts', 'export const MCP_URL')
  has('src/views/AgentsView.vue', 'MCP_URL')
  has('src/views/AgentsView.vue', 'useClipboard')
  has('src/views/AgentsView.vue', '删除配对')
  has('src/views/AgentsView.vue', 'saveRename')
  has('src/views/AgentsView.vue', '最后活跃')
})

test('配对弹窗：6 位数字逐位翻入 + 呼吸光晕 + 倒计时 MM:SS', () => {
  has('src/styles/main.css', '@keyframes flipIn')
  has('src/styles/main.css', 'animation: flipIn')               // 定义过不等于用过
  has('src/components/PairingModal.vue', 'class="cell"')        // 组件与 CSS 的接合点是 .cell
  has('src/styles/main.css', '@keyframes breathe')
  has('src/components/PairingModal.vue', 'formatCountdown')
  has('src/components/PairingModal.vue', 'PAIR_CODE_LENGTH')
  has('src/components/PairingModal.vue', '生成配对码')
})

test('自动化 Tab：启用/归档子 Tab + 待批红角标置顶 + Agent 折叠分组 + 卡片要素', () => {
  has('src/views/AutomationsView.vue', '待批操作')
  has('src/views/AutomationsView.vue', 'toggleFold')
  has('src/views/AutomationsView.vue', 'badge')
  has('src/components/AutomationCard.vue', '预演效果')
  has('src/components/AutomationCard.vue', '试演期')
  has('src/components/AutomationCard.vue', '最近触发')
  has('src/components/AutomationCard.vue', 'trigger_7d')
})

test('授权码 Tab：长期码列表 + 短期码滑块 5–30 分钟 + 实时 MM:SS 倒计时', () => {
  has('src/views/AuthCodesView.vue', 'SHORT_MIN_MINUTES')
  has('src/views/AuthCodesView.vue', 'SHORT_MAX_MINUTES')
  has('src/views/AuthCodesView.vue', '<n-slider')
  has('src/views/AuthCodesView.vue', 'formatCountdown')
  has('src/views/AuthCodesView.vue', '长期码')
  has('src/views/AuthCodesView.vue', '短期码')
})

test('登录页：居中卡片 + 用户名密码', () => {
  has('src/views/LoginView.vue', '用户名')
  has('src/views/LoginView.vue', '密码')
  has('src/views/LoginView.vue', 'min-height: 100dvh')
})

test('§8-3 移动端 375px：不写死超宽固定尺寸，长文本可断行', () => {
  const css = read('src/styles/main.css')
  assert.ok(css.includes('@media (max-width: 380px)'))
  assert.ok(css.includes('max-width: 320px'))       // 读数格上限，375px 内不溢出
  // 负向前瞻写在 `width:` 之后，挡不住写在它前面的 `max-`/`min-` 前缀：
  // HEAD 实测这条把上一行刚刚要求必须在位的 `max-width: 320px` 判成红（自相矛盾的量具）。
  assert.ok(!/(?<![-\w])width:\s*\d{3,}px/.test(css))
  has('src/views/AgentsView.vue', 'overflow-wrap: anywhere')
  has('src/components/AutomationCard.vue', 'overflow-wrap: anywhere')
})

test('裁定 20261005 §一 Q3：mock 缺省档 = 真后端，且这条判定全树只有一处', () => {
  // 交付构建漏写 env 时，`!== 'false'` 会静默上线 mock（两棵树原先缺省档还相反）。
  const env = read('src/api/env.ts')
  assert.ok(env.includes("export const USE_MOCK = src.VITE_USE_MOCK === 'true'"),
    '缺省档不是「显式 true 才 mock」：整条导出语句必须逐字在位，换回 !== false 形状即红')
  // 全树只有 env.ts 允许碰 `import.meta`（宿主注入面的唯一接缝）。判据看的是代码形状不是字样：
  // `api/index.ts` 的模块注释里本来就该写着 VITE_USE_MOCK 这个键名，那不算第二个真源。
  for (const f of ['src/api/index.ts', 'src/api/http.ts', 'src/stores/main.ts', 'src/api/mock.ts']) {
    assert.ok(!read(f).includes('import.meta'), `${f} 又自己读了一遍宿主注入的 env ⇒ 缺省档长出第二个真源`)
  }
  assert.ok(read('.env.production').includes('VITE_USE_MOCK=false'), '生产档没显式关掉 mock')
})

test('判据跑法：store 与 API 层不碰 DOM（裸 node 里没有 document）', () => {
  // HEAD 的 7 条红里有一条就是这个：`stores/main.ts` 的 applyTheme() 写 documentElement，
  // npm test 的跑法（真 node 进程、没有 jsdom）加载即红。半桩也不行——实测给
  // `globalThis.document = { documentElement: {...} }` 会让 @vue/runtime-dom 在导入期炸
  // `doc.createElement is not a function`。所以这条钉的是「这些模块可被非 DOM 宿主加载」。
  for (const f of ['src/stores/main.ts', 'src/api/index.ts', 'src/api/env.ts', 'src/api/http.ts', 'src/api/mock.ts']) {
    assert.ok(!/(document|window)\s*\./.test(read(f)), `${f} 直接碰 DOM 面 ⇒ 判据在裸 node 下加载即红`)
  }
})

test('主题开关的落点：store 出状态、App.vue 写 DOM（两边各钉一处，断一侧就红）', () => {
  // data-theme 从 store 搬进 App.vue 之后，判据跑法看不到 .vue 文件（node 解析不了 SFC），
  // 所以这条静态钉住接合点：store 仍然导出 darkMode，App.vue 仍然是唯一的 DOM 写者。
  has('src/stores/main.ts', 'darkMode,')
  const app = read('src/App.vue')
  const themeLine = app.split('\n').find((l) => l.includes("setAttribute('data-theme'"))
  assert.ok(themeLine, 'App.vue 不再写 data-theme ⇒ 主题开关没人落地')
  // 变异实测：把 watcher 整行注释掉，字符串仍然在文件里 ⇒ 只查字面量的断言会假绿。
  assert.ok(!themeLine.trimStart().startsWith('//'), `data-theme 那行是死的：${themeLine.trim()}`)
})

test('§8-1 PWA：manifest standalone + workbox 预缓存已配置', () => {
  has('vite.config.ts', 'VitePWA')
  has('vite.config.ts', 'display: \'standalone\'')
  has('vite.config.ts', 'globPatterns')
})