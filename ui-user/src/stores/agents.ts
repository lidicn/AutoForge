import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import type { Agent } from '../types/api'

export const useAgentsStore = defineStore('agents', () => {
  const agents = ref<Agent[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const r = await api.getAgents()
      agents.value = r.agents
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function remove(id: string) {
    await api.deleteAgent(id)
    await load()
  }

  async function rename(id: string, name: string) {
    await api.renameAgent(id, name)
    await load()
  }

  return { agents, loading, error, load, remove, rename }
})
