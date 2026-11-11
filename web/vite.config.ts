/// <reference types="vitest/config" />
import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { VitePWA } from "vite-plugin-pwa"

// The API serves this app at /ui/ (same address as the API, so no cross-site requests).
// Dev: `npm run dev` proxies API calls to HT_DEV_API (default: a local API on 18765).
// HT_DEV_LOGIN pretends to be the Tailscale login stamp, for a local API only.
const api = process.env.HT_DEV_API ?? "http://127.0.0.1:18765"
const devLogin = process.env.HT_DEV_LOGIN

export default defineConfig({
  base: "/ui/",
  plugins: [
    react(),
    tailwindcss(),
    // Service worker: keeps the app's own files on the device so it opens even when the server can't be reached
    // (data comes from the saved copy then). It never caches API responses; TanStack Query does that.
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: false,
      manifest: false,
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg}", "**/inter-latin-*.woff2"],
        navigateFallback: "/ui/index.html",
        navigateFallbackAllowlist: [/^\/ui\//],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
        // a new version takes over right away (the page then reloads into it) instead of waiting for every tab to close
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  define: { __BUILD_TIME__: JSON.stringify(new Date().toISOString().slice(0, 19).replace("T", " ") + " UTC") },
  build: { chunkSizeWarningLimit: 1600 }, // Plotly (~1.5 MB) is its own file, loaded only when a chart shows
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "^/(?!ui(/|$)|@|node_modules|src/)": {
        target: api,
        changeOrigin: true,
        configure: (proxy) => {
          if (devLogin) proxy.on("proxyReq", (req) => req.setHeader("Tailscale-User-Login", devLogin))
        },
      },
    },
  },
  test: { environment: "node" },
})
