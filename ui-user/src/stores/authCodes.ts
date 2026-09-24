import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import type { AuthCode } from '../types/api'

export const useAuthCodesStore = defineStore('authCodes', () => {
  const codes = ref<AuthCode[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const r = await api.listAuthCodes()
      codes.value = r.codes
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function create(kind: 'long' | 'short', ttl_minutes?: number) {
    await api.createAuthCode(kind, ttl_minutes)
    await load()
  }

  async function revoke(code: string) {
    await api.revokeAuthCode(code)
    await load()
  }

  return { codes, loading, error, load, create, revoke }
})
