import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  // 部署在同源服务层的 /mimo/ 子路径下（`forge serve --ui-user-dir`，见 af_api.UI_USER_PREFIX）。
  // base / start_url / scope 三处必须与那个常量同值：对不上时页面 200 但资源 404（白屏）。
  // 这条同源由 tests/unit/test_ui_user_mount.py 当场核对。
  base: '/mimo/',
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
        start_url: '/mimo/',
        scope: '/mimo/',
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
