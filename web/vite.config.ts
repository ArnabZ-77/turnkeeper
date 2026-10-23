/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI app serves `app/static` (index.html + /assets) in production. In development
// Vite serves the SPA and proxies the API, webhook and MCP paths to the backend.
const BACKEND = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
    sourcemap: false,
    // The Bryntum trial bundle is large and unminified by design; split it so the app chunk stays small.
    chunkSizeWarningLimit: 6000,
    rollupOptions: {
      output: {
        manualChunks: {
          bryntum: ["@bryntum/scheduler"],
          react: ["react", "react-dom"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: false },
      "/webhooks": { target: BACKEND, changeOrigin: false },
      "/mcp": { target: BACKEND, changeOrigin: false },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
