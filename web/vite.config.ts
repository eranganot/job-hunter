import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Base path: the SPA is served under /app on the Python backend.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Dev proxy not needed; SPA calls same-origin /api/* in production.
  },
  server: {
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
});
