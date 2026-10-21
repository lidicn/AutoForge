<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NInput } from 'naive-ui'
import { api, TOKEN_KEY } from '@/api/client'

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
  try {
    const data = await api.hasAdmin()
    // 没有管理员 → 显示注册页（与用户端 /mimo 登录页同一后端判定）
    isRegister.value = !data.has_admin
  } catch {
    // 接口不可用时默认登录页
    isRegister.value = false
  } finally {
    checking.value = false
  }
})

async function submit() {
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
    const res = isRegister.value
      ? await api.authRegister(username.value, password.value)
      : await api.authLogin(username.value, password.value)
    localStorage.setItem(TOKEN_KEY, res.token)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/overview'
    await router.replace(redirect)
  } catch (e) {
    error.value = String(e instanceof Error ? e.message : e)
  } finally {
    loading.value = false
  }
}

function switchToLogin() {
  isRegister.value = false
  error.value = ''
  confirmPassword.value = ''
}
</script>

<template>
  <main class="login">
    <section class="card">
      <header class="brand">
        <div class="mark">AF</div>
        <h1>AutoForge 控制台</h1>
        <p class="sub">开发者视角 · Agent 为中心的智能家居自动化平台</p>
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
        <span class="hint">登录令牌仅存于本浏览器，24h 过期后重新登录</span>
      </footer>
    </section>
  </main>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.card {
  width: 100%;
  max-width: 380px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 4px 24px rgba(15, 23, 42, 0.08);
  padding: 30px 26px 20px;
}
.brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  margin-bottom: 20px;
}
.mark {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, #6366f1, #4338ca);
  color: #fff;
  font-weight: 800;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #1f2937;
}
.sub {
  margin: 0;
  font-size: 12px;
  color: #8b93a7;
}
.checking {
  text-align: center;
  color: #8b93a7;
  padding: 20px 0;
  font-size: 13px;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.form-title {
  margin: 0 0 2px;
  font-size: 15px;
  font-weight: 600;
}
.form-hint {
  margin: 0 0 6px;
  font-size: 12px;
  color: #8b93a7;
}
.lab {
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #8b93a7;
  margin-top: 6px;
}
.err {
  margin: 4px 0 0;
  font-size: 12px;
  color: #dc2626;
}
.form :deep(.n-button) {
  margin-top: 14px;
}
.switch-hint {
  margin: 10px 0 0;
  font-size: 12px;
  color: #8b93a7;
  text-align: center;
}
.switch-hint a {
  color: #4f46e5;
  text-decoration: none;
}
.foot {
  margin-top: 20px;
  padding-top: 14px;
  border-top: 1px solid #eef0f4;
  text-align: center;
}
.hint {
  font-size: 11px;
  color: #8b93a7;
}
</style>
