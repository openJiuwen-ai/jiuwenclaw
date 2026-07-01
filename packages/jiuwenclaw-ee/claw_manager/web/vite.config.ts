import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5273,
    strictPort: true,
    // 与生产 nginx 同一套路由前缀:/api→管理API、/idp→认证中心、/ws+/file-api→web后端。
    // 三段 SPA 路由(/auth /user /manager)由 vite 开发服务器自动回退 index.html。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
      '/idp': { target: 'http://127.0.0.1:8770', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:19000', ws: true, changeOrigin: true },
      '/file-api': { target: 'http://127.0.0.1:19000', changeOrigin: true },
    },
  },
})
