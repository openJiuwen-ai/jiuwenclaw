import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5274,
    strictPort: true,
    proxy: {
      '/observability': {
        target: 'http://127.0.0.1:9090',
        changeOrigin: true,
      },
    },
  },
});
