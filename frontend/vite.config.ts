import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev server proxies the backend so the browser sees one origin (no CORS), exactly like nginx does in Docker.
const API = process.env.VITE_PROXY_TARGET ?? "http://localhost:8000";
const proxied = ["/v1", "/health", "/ready", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxied.map((p) => [p, { target: API, changeOrigin: true }])),
  },
  preview: { port: 4173 },
  build: { sourcemap: true },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
