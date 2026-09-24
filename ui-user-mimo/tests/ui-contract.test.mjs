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
  has('src/api/mock.ts', 'http://192.168.2.200:8000/mcp')
  has('src/views/AgentsView.vue', 'MCP_URL')
  has('src/views/AgentsView.vue', 'useClipboard')
  has('src/views/AgentsView.vue', '删除配对')
  has('src/views/AgentsView.vue', 'saveRename')
  has('src/views/AgentsView.vue', '最后活跃')
})

test('配对弹窗：6 位数字逐位翻入 + 呼吸光晕 + 倒计时 MM:SS', () => {
  has('src/components/PairingModal.vue', 'flipIn')
  has('src/styles/main.css', '@keyframes flipIn')
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
  assert.ok(!/width:\s*(?!100%|auto|min|max)\d{3,}px/.test(css))
  has('src/views/AgentsView.vue', 'overflow-wrap: anywhere')
  has('src/components/AutomationCard.vue', 'overflow-wrap: anywhere')
})

test('§8-1 PWA：manifest standalone + workbox 预缓存已配置', () => {
  has('vite.config.ts', 'VitePWA')
  has('vite.config.ts', 'display: \'standalone\'')
  has('vite.config.ts', 'globPatterns')
})