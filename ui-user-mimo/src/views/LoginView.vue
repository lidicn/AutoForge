<script setup lang="ts">
import { ref } from 'vue'
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
const loading = ref(false)
const error = ref('')

async function submit () {
  if (loading.value) return
  error.value = ''
  loading.value = true
  try {
    await store.login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/agents'
    await router.replace(redirect)
  } catch (e) {
    error.value = errorMessage(e, '登录失败')
  } finally {
    loading.value = false
  }
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

      <form class="form" @submit.prevent="submit">
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
          placeholder="密码"
          autocomplete="current-password"
        />

        <p v-if="error" class="err" role="alert">{{ error }}</p>

        <n-button type="primary" block :loading="loading" @click="submit">登录</n-button>
      </form>

      <footer class="foot">
        <span class="wordmark">ForgeSight</span>
        <!-- 这句话以前无条件写「mock 模式」，交付构建（VITE_USE_MOCK=false，走真后端）也照显示：
             界面自报一种没在跑的数据来源，与「假部署」同一族。按 env.ts 的同一个开关分支。 -->
        <span v-if="mockMode" class="hint">mock 构建：数据来自内置假后端，不发请求</span>
        <span v-else class="hint">后端为轻量单 owner 登录（无用户表）：任意非空用户名与密码即可进入</span>
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
.brand { display: flex; flex-direction: column; align-items: center; gap: 6px; margin-bottom: 22px; }
.mark {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 13px; color: var(--amber);
  background: rgba(245, 158, 11, 0.10); border: 1px solid rgba(245, 158, 11, 0.28);
  box-shadow: 0 0 24px rgba(245, 158, 11, 0.18);
}
.brand h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.02em; }
.sub { margin: 0; font-size: 12px; color: var(--ink-3); }
.form { display: flex; flex-direction: column; gap: 8px; }
.lab { font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-3); margin-top: 6px; }
.err { margin: 4px 0 0; font-size: 12px; color: var(--bad); }
.form :deep(.n-button) { margin-top: 14px; }
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