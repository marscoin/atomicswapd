import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Builds into the Python package, which serves it at /.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../atomicswapd/web', emptyOutDir: true, chunkSizeWarningLimit: 800 },
  server: { proxy: { '/api': 'http://localhost:8787' } },
})
