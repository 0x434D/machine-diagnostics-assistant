import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser only ever talks to its own origin. In development that is this dev server,
// in the container it is nginx, and both forward /api/plant/* to the simulator's
// in-process HMI server — so the simulator configures no CORS and no URL is baked into
// the bundle at build time.
//
// The default target is localhost because the simulator's HMI port is deliberately not
// published to the host (plant/compose.yml publishes exactly one port for the simulator,
// 4840, and test_compose_invariants pins that count): `pnpm dev` is for a simulator run
// on the host with `python -m simulator.server`. Point VITE_PLANT_URL elsewhere for
// anything else.
const proxy = {
  "/api/plant": {
    target: process.env.VITE_PLANT_URL ?? "http://localhost:8200",
    rewrite: (path: string) => path.replace(/^\/api\/plant/, ""),
    changeOrigin: true,
    // The snapshot stream is a WebSocket; without this the dev server answers the
    // upgrade itself and the screen never receives a frame.
    ws: true,
  },
};

export default defineConfig({
  plugins: [react()],
  // Not 5173: that is the diagnostics UI's, and both dev servers are run at once the
  // first time anyone compares what the plant is doing with what the diagnostics stack
  // says about it.
  server: { port: 5174, proxy },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    // Handbook §2: vitest 5 defaults this to true. Stated so that reading the config tells
    // you what happens between tests, rather than requiring you to know the default.
    clearMocks: true,
  },
});
