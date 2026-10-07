import { createRouter, createWebHistory } from 'vue-router'
import { useMainStore } from './stores/main.ts'

export const router = createRouter({
  // base 必须跟着构建期 base 走：部署在子路径时不带参数的 history 会把地址写成站点根，
  // 首屏之后刷新就落到另一张脸的 index 上（服务端 catch-all 送回的是 /mimo 那份，救不了）。
  history: createWebHistory(import.meta.env.BASE_URL),
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
