import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * 产物由 server 托管在 /admin/ 子路径下，因此 base 用相对路径 './'。
 * dev 时把 /api 代理到本地 server（默认 8104）。
 */
export default defineConfig({
  base: './',
  plugins: [react()],
  resolve: {
    alias: {
      // tsconfig paths 之外的兜底：让 @classfix/shared 在 vite 里也能解析
      '@classfix/shared': fileURLToPath(new URL('../shared/index.js', import.meta.url)),
    },
  },
  server: {
    port: 5273,
    proxy: {
      '/api': {
        target: 'http://localhost:8104',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
