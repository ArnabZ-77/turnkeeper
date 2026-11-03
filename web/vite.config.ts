/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The FastAPI app serves `app/static` (index.html + /assets) in production. In development
// Vite serves the SPA and proxies the API, webhook and MCP paths to the backend.
const BACKEND = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  base: "/",
  optimizeDeps: {
    include: ["@bryntum/scheduler", "@bryntum/scheduler-react"],
  },
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
    sourcemap: false,
    // The Bryntum trial bundle is large and unminified by design; split it so the app chunk stays small.
    chunkSizeWarningLimit: 6000,
    rollupOptions: {
      output: {
        // Only the scheduler's JS goes in the bryntum chunk. The two small Stockholm theme files are
        // imported `?inline` by theme/useTheme.ts on every page; if they landed in this chunk, every
        // route (the member passbook too) would preload 3.8 MB of scheduler it never runs.
        // React gets its own chunk for the same reason: Rollup otherwise pulls a manual chunk's
        // dependencies (bryntum-react depends on react) into it, and the entry would import React from there.
        manualChunks(id) {
          if (id.includes("/node_modules/@bryntum/") && !/\.css(\?|$)/.test(id)) return "bryntum";
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(id)) return "react";
          return undefined;
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
      // PayPal returns to these server routes (they answer 303 to the SPA's /join/{invite}?status=...).
      "^/join/[^/]+/(return|cancel)": { target: BACKEND, changeOrigin: false },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
