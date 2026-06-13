import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8008',
      '/data': 'http://127.0.0.1:8008',
      '/ws': { target: 'ws://127.0.0.1:8008', ws: true },
    },
  },
  build: { outDir: 'dist' },
})
