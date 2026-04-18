import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

/**
 * Vite dev config for apps/web.
 *
 * All `/api/*` calls are proxied to the FastAPI backend (run-backend.py).
 * In production the backend serves the frontend as static files, so the proxy
 * is a dev-only convenience.
 *
 * ENV:
 *   ICR_VIZ_API_URL   override for the backend target (default: http://127.0.0.1:8000)
 *   DISABLE_HMR=true  disable HMR (useful for some CI screenshots)
 */
export default defineConfig(() => {
  const backendTarget = process.env.ICR_VIZ_API_URL ?? 'http://127.0.0.1:8000';

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 3000,
      host: '0.0.0.0',
      hmr: process.env.DISABLE_HMR !== 'true',
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: true,
          // Keep WebSocket paths working (needed from i4 onward for operator.progress)
          ws: true,
        },
      },
    },
  };
});
