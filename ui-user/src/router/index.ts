import { createRouter, createWebHashHistory } from 'vue-router'
import AgentView from '../views/AgentView.vue'
import AutomationView from '../views/AutomationView.vue'
import AuthCodeView from '../views/AuthCodeView.vue'

const routes = [
  { path: '/', redirect: '/agent' },
  { path: '/agent', name: 'agent', component: AgentView, meta: { title: 'Agent' } },
  { path: '/automations', name: 'automations', component: AutomationView, meta: { title: '自动化' } },
  { path: '/auth-codes', name: 'auth-codes', component: AuthCodeView, meta: { title: '授权码' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
