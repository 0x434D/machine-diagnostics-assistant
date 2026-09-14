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
//
// PLANT_HMI_SERVER_PORT is the same variable simulator.config reads and the same one the
// container's nginx template is rendered from, so moving the server moves this with it.
// The literal below is the fallback of last resort and is pinned against
// Settings.hmi_server_port by simulator/tests/test_hmi.py.
const port = process.env.PLANT_HMI_SERVER_PORT ?? "8200";

const proxy = {
  "/api/plant": {
    target: process.env.VITE_PLANT_URL ?? `http://localhost:${port}`,
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
    // vitest stubs every CSS import to an empty string by default, `?raw` included --
    // so line.test.tsx's assertion that each of the five categories has a rule silently
    // read "" and passed nothing. It is the one thing on this screen carrying meaning,
    // and a test of it that cannot fail is worse than no test.
    css: true,
  },
});
