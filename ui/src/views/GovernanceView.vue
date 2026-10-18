<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NInput, NSpace, NTag, NText, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { facade } from '@/api'
import type { CredentialsResponse, SubjectItem, WhoamiResponse } from '@/types/api'

const message = useMessage()

// ── A 类 AF 访问密钥（存 localStorage，随请求带 Authorization Bearer）──
const token = ref(localStorage.getItem('af_token') || '')
function saveToken() {
  if (token.value.trim()) localStorage.setItem('af_token', token.value.trim())
  else localStorage.removeItem('af_token')
  message.success('访问密钥已保存（仅存于本浏览器）')
}

// ── whoami ──
const whoami = ref<WhoamiResponse | null>(null)
// ── credentials ──
const creds = ref<CredentialsResponse | null>(null)
const haToken = ref('')
const apiToken = ref('')
const savingCreds = ref(false)
// ── subjects ──
const subjects = ref<SubjectItem[]>([])
const revokeTarget = ref('')
const revoking = ref(false)

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
    await facade.updateCredentials(
      haToken.value.trim() || undefined,
      apiToken.value.trim() || undefined,
    )
    haToken.value = ''
    apiToken.value = ''
    message.success('凭据已更新（connection_revision +1，免重启生效）')
    await load()
  } catch (e) {
    message.error(`更新失败：${String(e)}`)
  } finally {
    savingCreds.value = false
  }
}

async function revoke() {
  if (!revokeTarget.value.trim()) {
    message.warning('请填写要撤销的令牌')
    return
  }
  revoking.value = true
  try {
    const res = await facade.revokeToken(revokeTarget.value.trim())
    message.success(res.data.revoked ? '令牌已即时撤销' : '未找到该令牌')
    revokeTarget.value = ''
    await load()
  } catch (e) {
    message.error(`撤销失败：${String(e)}`)
  } finally {
    revoking.value = false
  }
}

const subjectColumns: DataTableColumns<SubjectItem> = [
  {
    title: '主体 (subject)',
    key: 'subject',
    render: (row) => h('code', { style: 'font-size:13px' }, row.subject),
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

    <n-card title="AF 访问密钥 · 登录 / 调用 AF（A 类）" :bordered="false" class="block">
      <n-space vertical :size="12">
        <n-text depth="3">
          这是「谁能进入 AF」的密钥：填服务端 <code>AF_ACCESS_TOKENS</code>（旧 <code>AUTOFORGE_TOKENS</code>）
          里的某一条，随请求带 <code>Authorization: Bearer</code>。只保存在本浏览器，与下面的 HA 连接令牌无关。
        </n-text>
        <n-input
          v-model:value="token"
          type="password"
          show-password-on="click"
          placeholder="粘贴 AF 访问密钥（AF_ACCESS_TOKENS 里的某条 Bearer 值）"
          style="max-width: 460px"
        />
        <div><n-button type="primary" size="small" @click="saveToken">保存访问密钥</n-button></div>
        <n-alert v-if="whoami" type="success" :title="`当前身份：${whoami.subject}`">
          权限范围：{{ whoami.scopes.join(', ') || '（无）' }}
        </n-alert>
        <n-alert v-else type="warning" title="未携带有效访问密钥">当前请求未认证（服务端可能未开启鉴权，或访问密钥无效）。</n-alert>
      </n-space>
    </n-card>

    <n-card title="Home Assistant 连接令牌 · AF 出站用（B 类）" :bordered="false" class="block">
      <n-space vertical :size="12">
        <div v-if="creds">
          <div>HA 长期访问令牌（AF 连 Home Assistant 下发设备）：<code>{{ creds.ha_token || '（空）' }}</code></div>
          <div>旧版 AF 单密钥（一般留空，登录请用上方 A 类访问密钥）：<code>{{ creds.api_token || '（空）' }}</code></div>
          <div>连接代数 connection_revision：<b>{{ creds.connection_revision }}</b></div>
        </div>
        <n-text depth="3">留空表示不修改；提交后 connection_revision +1，所有依赖方即时生效，无需重启。</n-text>
        <n-input v-model:value="haToken" placeholder="新 HA 长期访问令牌（HA_LONG_LIVED_TOKEN，留空=不改）" style="max-width: 460px" />
        <n-input
          v-model:value="apiToken"
          type="password"
          show-password-on="click"
          placeholder="旧版 AF 单密钥（一般留空，留空=不改）"
          style="max-width: 460px"
        />
        <div><n-button type="primary" size="small" :loading="savingCreds" @click="saveCredentials">更新凭据</n-button></div>
      </n-space>
    </n-card>

    <n-card title="已注册令牌主体" :bordered="false" class="block">
      <n-data-table
        :columns="subjectColumns"
        :data="subjects"
        :bordered="false"
        :row-key="(row: SubjectItem) => row.subject"
      />
    </n-card>

    <n-card title="撤销令牌" :bordered="false" class="block">
      <n-space vertical :size="12">
        <n-input
          v-model:value="revokeTarget"
          placeholder="粘贴要撤销的令牌字符串"
          style="max-width: 460px"
        />
        <div>
          <n-button type="error" size="small" :loading="revoking" @click="revoke">即时撤销</n-button>
        </div>
        <n-text depth="3">撤销即时生效并落盘，被撤令牌后续请求返回 401。</n-text>
      </n-space>
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
