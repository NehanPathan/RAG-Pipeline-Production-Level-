import path from "node:path"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    rollupOptions: {
      output: {
        // Split by change rate as much as by size: our own code churns, the
        // vendor chunks do not, so a deploy invalidates as little of the
        // user's cache as possible. `charts` is the big one — Recharts plus
        // its d3 dependencies — and is only reached from Overview and the
        // ops pages, both of which are lazy.
        manualChunks(id) {
          if (!id.includes("node_modules")) return
          if (id.includes("recharts") || id.includes("/d3-") || id.includes("victory-vendor")) {
            return "charts"
          }
          if (id.includes("@radix-ui") || id.includes("cmdk") || id.includes("sonner")) {
            return "ui"
          }
          if (id.includes("lucide-react")) return "icons"
          if (
            id.includes("/react/") ||
            id.includes("/react-dom/") ||
            id.includes("react-router") ||
            id.includes("@tanstack")
          ) {
            return "vendor"
          }
        },
      },
    },
  },
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    // Inside a container the server must bind 0.0.0.0, or Docker's port
    // publish reaches nothing.
    host: process.env.VITE_HOST ?? "localhost",
    // When the dev server runs in a container the browser reaches it on the
    // published host port, but Vite's HMR client is told the *container*
    // port — so the websocket connects to the wrong place and hot reload
    // silently stops working while the page still loads. `make dev` sets
    // this to the published port.
    hmr: process.env.VITE_HMR_CLIENT_PORT
      ? { clientPort: Number(process.env.VITE_HMR_CLIENT_PORT) }
      : undefined,
    // The FastAPI app already allows all origins in development, but proxying
    // keeps the browser on one origin so cookies and the X-Trace-Id response
    // header behave the same in dev as they will behind a reverse proxy.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
})
