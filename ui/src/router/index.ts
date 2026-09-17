import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/overview' },
    { path: '/overview', name: 'overview', component: () => import('@/views/OverviewView.vue') },
    { path: '/automations', name: 'automations', component: () => import('@/views/AutomationsListView.vue') },
    { path: '/automations/:name', name: 'automation-detail', component: () => import('@/views/AutomationDetailView.vue') },
    { path: '/devices', name: 'devices', component: () => import('@/views/DevicesView.vue') },
    { path: '/data', name: 'data', component: () => import('@/views/DataView.vue') },
    { path: '/simulation', name: 'simulation', component: () => import('@/views/SimulationView.vue') },
    { path: '/confidence', name: 'confidence', component: () => import('@/views/ConfidenceView.vue') },
    { path: '/versions', name: 'versions', component: () => import('@/views/VersionsView.vue') },
    { path: '/spec-editor', name: 'spec-editor', component: () => import('@/views/SpecEditorView.vue') },
    { path: '/faults', name: 'faults', component: () => import('@/views/FaultsView.vue') },
    // ── v1.7.0-b 治理与实时（批次 B）──
    { path: '/pending', name: 'pending', component: () => import('@/views/PendingView.vue') },
    { path: '/live', name: 'live', component: () => import('@/views/LiveView.vue') },
    { path: '/governance', name: 'governance', component: () => import('@/views/GovernanceView.vue') },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
})

export default router
