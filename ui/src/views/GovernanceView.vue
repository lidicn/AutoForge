<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NInput, NSpace, NTag, NText, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { CredentialsResponse, SubjectItem, WhoamiResponse } from '@/types/api'

const message = useMessage()

// ── whoami ──
const whoami = ref<WhoamiResponse | null>(null)
// ── credentials ──
const creds = ref<CredentialsResponse | null>(null)
const haToken = ref('')
const savingCreds = ref(false)
// ── subjects ──
const subjects = ref<SubjectItem[]>([])
const revokingSubject = ref('')

const error = ref('')

async function load() {
  error.value = ''
  try {
    const [w, c, s] = await Promise.all([
      facade.whoami().catch(() => null),
      facade.credentials().catch(() => null),
      facade.subjects().catch(() => null),
    ])
    whoami.value = w?.data ?? null
    creds.value = c?.data ?? null
    subjects.value = s?.data?.subjects ?? []
  } catch (e) {
    error.value = String(e)
  }
}

async function saveCredentials() {
  savingCreds.value = true
  try {
    await facade.updateCredentials(haToken.value.trim() || undefined)
    haToken.value = ''
    message.success('凭据已更新（connection_revision +1，免重启生效）')
    await load()
  } catch (e) {
    message.error(`更新失败：${String(e)}`)
  } finally {
    savingCreds.value = false
  }
}

// 批 3：治理页按主体撤销（owner 面专属；非 owner 调用由后端 403）。
async function revokeRow(row: SubjectItem) {
  revokingSubject.value = row.subject
  try {
    const res = await facade.revokeBySubject(row.subject)
    message.success(res.data.revoked ? `已撤销 ${row.subject} 名下令牌` : '未找到该主体')
    await load()
  } catch (e) {
    message.error(`撤销失败：${String(e)}`)
  } finally {
    revokingSubject.value = ''
  }
}

const KIND_LABEL: Record<string, { text: string; type: 'success' | 'info' | 'warning' }> = {
  login: { text: '登录态', type: 'success' },
  agent: { text: 'Agent', type: 'info' },
  developer: { text: '开发者', type: 'warning' },
}

function fmtTtl(expires_at?: number | null): string {
  if (!expires_at) return '不过期'
  const ms = expires_at * 1000 - Date.now()
  if (ms <= 0) return '已过期'
  const d = Math.floor(ms / 86400000)
  const h = Math.floor((ms % 86400000) / 3600000)
  return d > 0 ? `${d}天${h}时` : `${Math.max(1, Math.round(ms / 3600000))}时`
}

const subjectColumns: DataTableColumns<SubjectItem> = [
  {
    title: '主体 (subject)',
    key: 'subject',
    render: (row) => h('code', { style: 'font-size:13px' }, row.subject),
  },
  {
    title: '身份族',
    key: 'principal_kind',
    width: 100,
    render: (row) => {
      const k = KIND_LABEL[row.principal_kind || 'agent'] || { text: row.principal_kind || 'agent', type: 'info' as const }
      return h(NTag, { size: 'small', bordered: false, type: k.type }, { default: () => k.text })
    },
  },
  {
    title: '权限范围 (scopes)',
    key: 'scopes',
    render: (row) =>
      h(
        'div',
        { style: 'display:flex;gap:6px;flex-wrap:wrap' },
        (row.scopes || []).map((s) =>
          h(NTag, { size: 'small', bordered: false, type: 'success' }, { default: () => s }),
        ),
      ),
  },
  {
    title: 'TTL',
    key: 'expires_at',
    width: 90,
    render: (row) => fmtTtl(row.expires_at),
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row) =>
      h(
        NButton,
        {
          size: 'tiny',
          type: 'error',
          tertiary: true,
          loading: revokingSubject.value === row.subject,
          onClick: () => revokeRow(row),
        },
        { default: () => '撤销' },
      ),
  },
]

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>治理 / 设置</h1>
      <p class="sub">凭据热重载、令牌管理与当前身份（v1.4.0 / v0.8.0 治理面）</p>
    </div>

    <n-alert v-if="error" type="error" title="加载失败" style="margin-bottom: 16px">{{ error }}</n-alert>

    <n-card title="当前身份" :bordered="false" class="block">
      <n-alert v-if="whoami" type="success" :title="`当前身份：${whoami.subject}`">
        权限范围：{{ whoami.scopes.join(', ') || '（无）' }}
      </n-alert>
      <n-alert v-else type="warning" title="未携带有效身份">
        当前请求未认证（请从左上角登录入口登录；浏览器不再粘贴长期 A 类密钥）。
      </n-alert>
    </n-card>

    <n-card title="Home Assistant 连接令牌 · AF 出站用（B 类）" :bordered="false" class="block">
      <n-space vertical :size="12">
        <div v-if="creds">
          <div>HA 长期访问令牌（AF 连 Home Assistant 下发设备）：<code>{{ creds.ha_token || '（空）' }}</code></div>
          <div>连接代数 connection_revision：<b>{{ creds.connection_revision }}</b></div>
        </div>
        <n-text depth="3">留空表示不修改；提交后 connection_revision +1，所有依赖方即时生效，无需重启。</n-text>
        <n-input v-model:value="haToken" placeholder="新 HA 长期访问令牌（HA_LONG_LIVED_TOKEN，留空=不改）" style="max-width: 460px" />
        <div><n-button type="primary" size="small" :loading="savingCreds" @click="saveCredentials">更新凭据</n-button></div>
        <n-text depth="3">
          注：旧版「AF 单密钥」（credentials.json 的 api_token 键）已无鉴权消费方——登录走左上角登录入口，此处不再展示该键。
        </n-text>
      </n-space>
    </n-card>

    <n-card title="已注册令牌主体" :bordered="false" class="block">
      <n-data-table
        :columns="subjectColumns"
        :data="subjects"
        :bordered="false"
        :row-key="(row: SubjectItem) => row.subject"
      />
      <n-text depth="3" style="margin-top: 8px; display: block">
        ⛔ 本表不展示令牌明文或摘要；「撤销」按主体吊销其名下全部令牌（owner 面可用）。
      </n-text>
    </n-card>
  </div>
</template>

<style scoped>
.page-head {
  margin-bottom: 20px;
}
.page-head h1 {
  margin: 0 0 4px;
  font-size: 26px;
}
.page-head .sub {
  margin: 0;
  color: #8b93a7;
  font-size: 14px;
}
.block {
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
</style>
