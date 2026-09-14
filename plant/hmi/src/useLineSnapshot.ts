import { useEffect, useState } from "react";

import type { LineSnapshot } from "./snapshot";

/** How long to wait before dialling again after the socket closes.
 *
 * The plant restarts on every `docker compose up`, and its catch-up takes minutes, so
 * this screen spends real time disconnected and must come back on its own. One second
 * is below the interval at which a human reaches for F5 and far above the rate at which
 * retrying costs anything.
 */
const RECONNECT_MS = 1000;

/** Same origin, forwarded by nginx or the Vite dev server. See nginx.conf. */
function socketUrl(): string {
  const url = new URL("api/plant/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export interface LineState {
  snapshot: LineSnapshot | null;
  connected: boolean;
}

/**
 * The line, as the simulator pushes it.
 *
 * `snapshot` is the last frame received and is kept across a disconnect on purpose:
 * what the line was doing a second ago is still the most useful thing on the screen,
 * and `connected` is what says not to trust it as current. Blanking the line the
 * instant the socket drops would make a page reload look like a line that had stopped.
 */
export function useLineSnapshot(): LineState {
  const [snapshot, setSnapshot] = useState<LineSnapshot | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      // A fresh socket each time, so its listeners go with it and there is nothing to
      // detach: a reconnect that reused the socket would stack a second handler on it.
      const dialled = new WebSocket(socketUrl());
      socket = dialled;
      dialled.addEventListener("open", () => {
        setConnected(true);
      });
      dialled.addEventListener("message", (event: MessageEvent<string>) => {
        setSnapshot(JSON.parse(event.data) as LineSnapshot);
      });
      // "close" fires for a refused connection too, so this is the only place that has
      // to schedule a retry — there is no separate error path to keep in step with it.
      dialled.addEventListener("close", () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, RECONNECT_MS);
      });
    };

    connect();
    return () => {
      // `closed` first: closing the socket fires onclose, which would otherwise
      // schedule a reconnect for a component that is going away. StrictMode mounts
      // twice in development, so this runs on a live socket every single time.
      closed = true;
      if (retry !== null) clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return { snapshot, connected };
}
