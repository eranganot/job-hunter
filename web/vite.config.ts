import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Base path: the SPA is served under /app on the Python backend.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/index.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/index.[ext]",
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
});
