/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    globals: true,
  },
  server: {
    // Required, not cosmetic: the API sets its auth cookie for localhost:8000
    // and has no CORS middleware, so a fetch from the 5173 dev origin straight
    // to :8000 would neither send nor receive the cookie. Proxying makes every
    // request same-origin from the browser's point of view.
    proxy: {
      '/auth': 'http://localhost:8000',
      '/documents': 'http://localhost:8000',
      '/jobs': 'http://localhost:8000',
    },
  },
})
