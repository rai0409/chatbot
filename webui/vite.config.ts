import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The console is served by the web API under /console/. In development, API calls go to the running server.
export default defineConfig({
  base: "/console/",
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
    // The screens are loaded on first use (src/routes.tsx); the libraries go to their own chunks, which change less
    // often than the console and stay in the browser cache across releases. Vite 8 bundles with Rolldown.
    chunkSizeWarningLimit: 250,
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: "react", test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/, priority: 20 },
            { name: "router", test: /[\\/]node_modules[\\/](react-router|react-router-dom|cookie|set-cookie-parser)[\\/]/, priority: 10 },
          ],
        },
      },
    },
  },
  server: { proxy: { "/admin": "http://localhost:8000", "/system": "http://localhost:8000" } },
  test: { environment: "jsdom", setupFiles: ["src/test/setup.ts"], include: ["src/**/*.test.{ts,tsx}"], restoreMocks: true },
});
