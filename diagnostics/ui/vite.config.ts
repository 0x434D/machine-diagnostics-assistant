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
  // The development issuer. It publishes no port in the container stack — it is on the
  // internal diag-net and nginx forwards to it there — so this default is for an issuer run
  // from the checkout: `uv run --package issuer uvicorn issuer.app:app --port 8003`.
  "/api/issuer": {
    target: process.env.VITE_ISSUER_URL ?? "http://localhost:8003",
    rewrite: (path: string) => path.replace(/^\/api\/issuer/, ""),
    changeOrigin: true,
  },
  // The edge gateway, for §7.2's plant status banner. Read-only: the banner asks `/status`
  // and the browser has no other business with the boundary process (§4.5). The default is
  // the port `diagnostics/compose.yml` publishes it on.
  "/api/gateway": {
    target: process.env.VITE_GATEWAY_URL ?? "http://localhost:8080",
    rewrite: (path: string) => path.replace(/^\/api\/gateway/, ""),
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
    // Vitest replaces every CSS import with an empty module by default, which also empties
    // a `?raw` one. `src/__tests__/layout.test.tsx` reads the stylesheet as text -- jsdom
    // performs no layout, so the only way to check that nothing demands more width than a
    // phone has is to read the declarations themselves -- and it reads nothing without this.
    css: true,
  },
});
