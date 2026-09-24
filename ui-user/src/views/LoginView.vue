<template>
  <div class="login-page">
    <div class="login-card">
      <div class="logo">
        <div class="logo-icon">
          <svg viewBox="0 0 48 48" width="48" height="48">
            <circle cx="24" cy="24" r="20" fill="none" stroke="#F59E0B" stroke-width="2.5"/>
            <path d="M24 14 L28 24 L24 34 L20 24 Z" fill="#F59E0B"/>
          </svg>
        </div>
        <h1>AutoForge</h1>
        <p class="subtitle">智能家居自动化</p>
      </div>

      <n-form @submit.prevent="handleLogin">
        <n-input
          v-model:value="username"
          placeholder="用户名"
          size="large"
          style="margin-bottom: 12px"
          :bordered="false"
          quaternary
        />
        <n-input
          v-model:value="password"
          type="password"
          placeholder="密码"
          size="large"
          style="margin-bottom: 20px"
          :bordered="false"
          quaternary
          @keyup.enter="handleLogin"
        />
        <n-button
          type="primary"
          size="large"
          block
          :loading="loading"
          @click="handleLogin"
        >
          登录
        </n-button>
      </n-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NButton, NForm, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

const username = ref('')
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!username.value || !password.value) {
    message.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push({ name: 'agents' })
  } catch {
    message.error('登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: linear-gradient(160deg, #fafafa 0%, #fef3c7 100%);
}
.login-card {
  width: 100%;
  max-width: 360px;
  background: white;
  border-radius: 20px;
  padding: 40px 28px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.08);
}
.logo {
  text-align: center;
  margin-bottom: 32px;
}
.logo-icon {
  display: flex;
  justify-content: center;
  margin-bottom: 12px;
}
.logo h1 {
  font-size: 28px;
  font-weight: 700;
  color: #1a1a1a;
}
.subtitle {
  font-size: 14px;
  color: #999;
  margin-top: 4px;
}
</style>
