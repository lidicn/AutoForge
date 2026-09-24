import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// ForgeSight 用户端：独立工程、独立端口，与开发面板 ui/ 互不影响。
export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg'],
      manifest: {
        name: 'ForgeSight · AutoForge 用户端',
        short_name: 'ForgeSight',
        description: 'AutoForge 家庭自动化 · 用户端',
        theme_color: '#F59E0B',
        background_color: '#FAFAFA',
        display: 'standalone',
        start_url: '/',
        icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any maskable' }],
      },
    }),
  ],
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    proxy: {
      // 开发联调：将 API 与配对 SSE 代理到后端（按需改 host/端口）
      '/api': { target: 'http://192.168.2.200:8787', changeOrigin: true },
      '/mcp': { target: 'http://192.168.2.200:8787', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false, emptyOutDir: false },
})
