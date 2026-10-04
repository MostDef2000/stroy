import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Split vendor code so the three.js stack is cached independently from
        // React. The "three" check MUST come first: @react-three/* ids contain
        // "react" too, and we want the whole 3D stack in one chunk.
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("three")) return "vendor-three";
            if (id.includes("react") || id.includes("scheduler")) return "vendor-react";
            return "vendor-misc";
          }
        }
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/ready": "http://127.0.0.1:8000"
    }
  }
});
