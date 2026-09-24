import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '@/api/client'
import type { User } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string | null>(localStorage.getItem('fs_token'))

  const isLoggedIn = computed(() => !!token.value)

  async function login(username: string, password: string) {
    const res = await api.login(username, password)
    user.value = res.user
    token.value = 'mock-token'
    localStorage.setItem('fs_token', token.value)
  }

  async function logout() {
    await api.logout()
    user.value = null
    token.value = null
    localStorage.removeItem('fs_token')
  }

  async function fetchMe() {
    if (token.value) {
      user.value = await api.me()
    }
  }

  return { user, isLoggedIn, login, logout, fetchMe }
})
