import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  // 部署在 NAS 反代的 /ui-user/ 子路径下（同源反代 /api，免 CORS）
  base: '/ui-user/',
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg', 'icon-maskable.svg'],
      manifest: {
        name: 'AutoForge 用户端',
        short_name: 'AutoForge',
        description: 'AutoForge 用户端控制台（ForgeSight）',
        lang: 'zh-CN',
        theme_color: '#14120F',
        background_color: '#14120F',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/ui-user/',
        scope: '/ui-user/',
        icons: [
          { src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
          { src: '/icon-maskable.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,woff2}'],
        navigateFallback: 'index.html',
      },
    }),
  ],
  server: {
    host: true,
    port: 5175,
    proxy: {
      '/api': { target: 'http://192.168.2.200:8787', changeOrigin: true },
      '/mcp': { target: 'http://192.168.2.200:8787', changeOrigin: true },
    },
  },
  build: {
    target: 'es2020',
    chunkSizeWarningLimit: 2000,
    rollupOptions: { output: { manualChunks: { naive: ['naive-ui'] } } },
  },
})
