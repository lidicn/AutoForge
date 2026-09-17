import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // 受限环境下 node 的 fs.rmSync 会被重定向到"安全删除"并在清空 outDir 时失败
    // （prepareOutDir → emptyDir 报错）。这里不自清空：产物文件名带 hash，
    // 旧文件残留无害，由部署环节负责清理。
    emptyOutDir: false,
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
})
