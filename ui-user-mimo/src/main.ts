import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router.ts'
import { useMainStore } from './stores/main.ts'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// 先恢复会话，再挂路由守卫，避免首屏被踢回登录页
const store = useMainStore(pinia)
store.bootstrap()
// 先恢复会话（有 token 才生效），再挂路由，避免首屏被踢回登录页
store.restore().finally(() => {
  app.use(router)
  app.mount('#app')
})
