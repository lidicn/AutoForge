import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api/client'
import type { Agent, Automation, AuthCode, PairRequest } from '@/types/api'

export const useMainStore = defineStore('main', () => {
  const agents = ref<Agent[]>([])
  const automations = ref<Automation[]>([])
  const authCodes = ref<AuthCode[]>([])
  const pairRequest = ref<PairRequest | null>(null)

  async function fetchAgents() {
    agents.value = await api.getAgents()
  }

  async function deleteAgent(id: string) {
    await api.deleteAgent(id)
    agents.value = agents.value.filter(a => a.agent_id !== id)
  }

  async function renameAgent(id: string, name: string) {
    await api.renameAgent(id, name)
    const a = agents.value.find(a => a.agent_id === id)
    if (a) a.name = name
  }

  async function fetchAutomations() {
    automations.value = await api.getAutomations()
  }

  async function toggleAutomation(id: string, enable: boolean) {
    await api.toggleAutomation(id, enable)
    const a = automations.value.find(a => a.id === id)
    if (a) a.status = enable ? 'enabled' : 'disabled'
  }

  async function archiveAutomation(id: string, archive: boolean) {
    await api.archiveAutomation(id, archive)
    const a = automations.value.find(a => a.id === id)
    if (a) a.archived = archive
  }

  async function deleteAutomation(id: string) {
    await api.deleteAutomation(id)
    automations.value = automations.value.filter(a => a.id !== id)
  }

  async function fetchAuthCodes() {
    authCodes.value = await api.getAuthCodes()
  }

  async function generateAuthCode(type: 'long' | 'short', durationMinutes?: number) {
    const code = await api.generateAuthCode(type, durationMinutes)
    authCodes.value.unshift(code)
    return code
  }

  async function deleteAuthCode(code: string) {
    await api.deleteAuthCode(code)
    authCodes.value = authCodes.value.filter(c => c.code !== code)
  }

  function showPairRequest(req: PairRequest) {
    pairRequest.value = req
  }

  function dismissPairRequest() {
    pairRequest.value = null
  }

  return {
    agents, automations, authCodes, pairRequest,
    fetchAgents, deleteAgent, renameAgent,
    fetchAutomations, toggleAutomation, archiveAutomation, deleteAutomation,
    fetchAuthCodes, generateAuthCode, deleteAuthCode,
    showPairRequest, dismissPairRequest,
  }
})
