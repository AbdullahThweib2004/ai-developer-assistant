/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiPort = env.VITE_API_PORT || "8765";
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      strictPort: true,
      // Dev only: forward /api to the local backend (started with --dev, which allows this origin).
      proxy: { "/api": { target: `http://127.0.0.1:${apiPort}`, changeOrigin: true } },
    },
    build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 700 },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.tsx", "src/**/*.test.ts"],
      css: false,
    },
  };
});
