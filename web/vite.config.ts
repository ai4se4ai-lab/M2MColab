import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The service (agentm2m serve) answers /api and /mcp; in dev, vite proxies to it.
const service = process.env.AGENTM2M_SERVICE ?? 'http://127.0.0.1:8765'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': service,
      '/mcp': service,
    },
  },
})
