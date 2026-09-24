import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api'
import type { AutomationGroup } from '../types/api'

export const useAutomationsStore = defineStore('automations', () => {
  const groups = ref<AutomationGroup[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const r = await api.listAutomations('agent')
      groups.value = r.groups || []
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function enable(name: string) {
    await api.enableAutomation(name)
    await load()
  }
  async function disable(name: string) {
    await api.disableAutomation(name)
    await load()
  }
  async function archive(name: string) {
    await api.archiveAutomation(name)
    await load()
  }
  async function unarchive(name: string) {
    await api.unarchiveAutomation(name)
    await load()
  }
  async function remove(name: string) {
    await api.deleteAutomation(name)
    await load()
  }

  return { groups, loading, error, load, enable, disable, archive, unarchive, remove }
})
