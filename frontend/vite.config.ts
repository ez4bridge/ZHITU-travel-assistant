import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:7862', '/_AMapService': 'http://127.0.0.1:7862' } },
  build: { sourcemap: false },
})
