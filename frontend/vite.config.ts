import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The app is built into the Python package, which serves it: `moot serve`.
// `npm run dev` serves it with live reload and passes /api on to a running `moot serve`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: process.env.MOOT_STATIC_DIR ?? "../src/moot/server/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        // The server refuses cross-site requests; through the proxy they are same-site.
        configure: (proxy) => proxy.on("proxyReq", (request) => request.removeHeader("origin")),
      },
    },
  },
  test: { environment: "node" },
});
