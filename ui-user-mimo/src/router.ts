import { createRouter, createWebHistory } from 'vue-router'
import { useMainStore } from './stores/main.ts'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    {
      path: '/',
      component: () => import('./views/MainLayout.vue'),
      children: [
        { path: '', redirect: '/agents' },
        { path: 'agents', name: 'agents', component: () => import('./views/AgentsView.vue') },
        { path: 'automations', name: 'automations', component: () => import('./views/AutomationsView.vue') },
        { path: 'auth-codes', name: 'auth-codes', component: () => import('./views/AuthCodesView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/agents' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  const store = useMainStore()
  if (to.name !== 'login' && !store.user) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && store.user) return { name: 'agents' }
  return true
})
