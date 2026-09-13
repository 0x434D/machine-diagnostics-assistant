import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser only ever talks to its own origin. In development that is this dev server,
// in the container it is nginx, and both forward /api/* to the two services — so there is
// no CORS configuration on either service and no build-time API URL baked into the bundle.
const proxy = {
  "/api/agent": {
    target: process.env.VITE_AGENT_URL ?? "http://localhost:8001",
    rewrite: (path: string) => path.replace(/^\/api\/agent/, ""),
    changeOrigin: true,
  },
  "/api/analysis": {
    target: process.env.VITE_ANALYSIS_URL ?? "http://localhost:8000",
    rewrite: (path: string) => path.replace(/^\/api\/analysis/, ""),
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    // Handbook §2: vitest 5 defaults this to true. Stated so that reading the config tells
    // you what happens between tests, rather than requiring you to know the default.
    clearMocks: true,
  },
});
