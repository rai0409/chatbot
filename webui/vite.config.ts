import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The console is served by the web API under /console/. In development, API calls go to the running server.
export default defineConfig({
  base: "/console/",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  server: { proxy: { "/admin": "http://localhost:8000", "/system": "http://localhost:8000" } },
  test: { environment: "jsdom", setupFiles: ["src/test/setup.ts"], include: ["src/**/*.test.{ts,tsx}"], restoreMocks: true },
});
