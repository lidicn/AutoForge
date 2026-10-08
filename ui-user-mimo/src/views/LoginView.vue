<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NInput } from 'naive-ui'
import UiIcon from '../components/UiIcon.vue'
import { USE_MOCK } from '../api/env.ts'
import { useMainStore } from '../stores/main.ts'
import { errorMessage } from '../logic/format.ts'

const mockMode = USE_MOCK
const store = useMainStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')
// 注册模式：false=登录，true=注册
const isRegister = ref(false)
const checking = ref(true)

onMounted(async () => {
  if (mockMode) {
    checking.value = false
    return
  }
  try {
    const res = await fetch('/api/auth/has-admin')
    const data = await res.json()
    // 没有管理员 → 显示注册页
    isRegister.value = !data.has_admin
  } catch {
    // 接口不可用时默认登录页
    isRegister.value = false
  } finally {
    checking.value = false
  }
})

async function submit () {
  if (loading.value) return
  error.value = ''
  if (isRegister.value) {
    if (password.value !== confirmPassword.value) {
      error.value = '两次输入的密码不一致'
      return
    }
    if (password.value.length < 6) {
      error.value = '密码至少 6 个字符'
      return
    }
    if (username.value.length < 2) {
      error.value = '用户名至少 2 个字符'
      return
    }
  }
  loading.value = true
  try {
    if (isRegister.value) {
      // 注册：调用注册接口，成功后自动登录
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.value, password: password.value })
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || '注册失败')
      }
      // 注册成功，走正常登录流程
      await store.login(username.value, password.value)
    } else {
      await store.login(username.value, password.value)
    }
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/agents'
    await router.replace(redirect)
  } catch (e) {
    error.value = errorMessage(e, isRegister.value ? '注册失败' : '登录失败')
  } finally {
    loading.value = false
  }
}

function switchToLogin () {
  isRegister.value = false
  error.value = ''
  confirmPassword.value = ''
}
</script>

<template>
  <main class="login">
    <section class="card box">
      <div class="ember-top" />
      <header class="brand">
        <span class="mark"><ui-icon name="flame" :size="22" /></span>
        <h1>AutoForge</h1>
        <p class="sub">用户端控制台 · 一处点火，全屋联动</p>
      </header>

      <div v-if="checking" class="checking">正在检查配置…</div>

      <form v-else class="form" @submit.prevent="submit">
        <h2 class="form-title">{{ isRegister ? '首次使用 · 设置账号' : '登录' }}</h2>
        <p v-if="isRegister" class="form-hint">设置管理员账号密码，之后用它登录。</p>

        <label class="lab" for="af-user">用户名</label>
        <n-input
          id="af-user"
          v-model:value="username"
          placeholder="用户名"
          :maxlength="32"
          autocomplete="username"
        />

        <label class="lab" for="af-pass">密码</label>
        <n-input
          id="af-pass"
          v-model:value="password"
          type="password"
          show-password-on="click"
          placeholder="至少 6 个字符"
          autocomplete="current-password"
        />

        <label v-if="isRegister" class="lab" for="af-pass2">确认密码</label>
        <n-input
          v-if="isRegister"
          id="af-pass2"
          v-model:value="confirmPassword"
          type="password"
          show-password-on="click"
          placeholder="再次输入密码"
          autocomplete="new-password"
        />

        <p v-if="error" class="err" role="alert">{{ error }}</p>

        <n-button type="primary" block :loading="loading" @click="submit">
          {{ isRegister ? '注册并登录' : '登录' }}
        </n-button>

        <p v-if="isRegister" class="switch-hint">
          已有账号？
          <a href="#" @click.prevent="switchToLogin">去登录</a>
        </p>
      </form>

      <footer class="foot">
        <span class="wordmark">ForgeSight</span>
        <span v-if="mockMode" class="hint">mock 构建：数据来自内置假后端，不发请求</span>
        <span v-else class="hint">{{ isRegister ? '首次使用请先设置账号' : '使用注册的账号密码登录' }}</span>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.login {
  min-height: 100dvh; display: flex; align-items: center; justify-content: center;
  padding: 24px var(--pad);
}
.box {
  position: relative; width: 100%; max-width: 360px; overflow: hidden;
  padding: 26px 20px 18px;
}
.ember-top {
  position: absolute; inset: 0 0 auto 0; height: 2px;
  background: linear-gradient(90deg, transparent, rgba(245, 158, 11, 0.9), transparent);
}
.brand { display: flex; flex-direction: column; align-items: center; gap: 6px; margin-bottom: 18px; }
.mark {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 13px; color: var(--amber);
  background: rgba(245, 158, 11, 0.10); border: 1px solid rgba(245, 158, 11, 0.28);
  box-shadow: 0 0 24px rgba(245, 158, 11, 0.18);
}
.brand h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.02em; }
.sub { margin: 0; font-size: 12px; color: var(--ink-3); }
.checking { text-align: center; color: var(--ink-3); padding: 20px 0; font-size: 13px; }
.form { display: flex; flex-direction: column; gap: 8px; }
.form-title { margin: 0 0 2px; font-size: 15px; font-weight: 600; }
.form-hint { margin: 0 0 6px; font-size: 12px; color: var(--ink-3); }
.lab { font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-3); margin-top: 6px; }
.err { margin: 4px 0 0; font-size: 12px; color: var(--bad); }
.form :deep(.n-button) { margin-top: 14px; }
.switch-hint { margin: 10px 0 0; font-size: 12px; color: var(--ink-3); text-align: center; }
.switch-hint a { color: var(--amber-2); text-decoration: none; }
.foot {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--line-soft);
}
.wordmark {
  font-family: var(--mono); font-size: 9px; letter-spacing: 0.34em;
  text-transform: uppercase; color: var(--ink-3);
}
.hint { font-size: 11px; color: var(--ink-3); }
.hint code { color: var(--amber-2); }
</style>
