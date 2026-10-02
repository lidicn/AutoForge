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
    { path: '/metrics', name: 'metrics', component: () => import('@/views/MetricsView.vue') },
    // ── v1.7.0-b 治理与实时（批次 B）──
    { path: '/pending', name: 'pending', component: () => import('@/views/PendingView.vue') },
    { path: '/live', name: 'live', component: () => import('@/views/LiveView.vue') },
    { path: '/governance', name: 'governance', component: () => import('@/views/GovernanceView.vue') },
    // v1.7.3 运行中 watch 实例
    { path: '/running', name: 'running', component: () => import('@/views/RunningView.vue') },
    // v2.1 F4 ③ 生产态验证证据（监护视图）
    { path: '/evidence', name: 'evidence', component: () => import('@/views/EvidenceView.vue') },
    // ── v2 M3 结构化 Ask 原生控件 + clarify 流程 ──
    { path: '/asks', name: 'asks', component: () => import('@/views/AsksView.vue') },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
})

export default router
