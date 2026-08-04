import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const envDir = path.resolve(__dirname, '../../../..')
  const env = loadEnv(mode, envDir, '')
  const allowLocalProvision =
    env.VITE_MANAGER_ALLOW_LOCAL_PROVISION ?? env.MANAGER_ALLOW_LOCAL_PROVISION ?? 'false'

  return {
    envDir,
    define: {
      'import.meta.env.VITE_MANAGER_ALLOW_LOCAL_PROVISION': JSON.stringify(allowLocalProvision),
    },
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 5273,
      strictPort: true,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8765',
          changeOrigin: true,
        },
      },
    },
  }
})
