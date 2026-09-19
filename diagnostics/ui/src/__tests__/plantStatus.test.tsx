/** §7.2's plant status banner, against what the gateway actually answers.
 *
 * The three situations the spec names — *connected · backfilling · plant offline, history to
 * 03:14* — have to be three different readings on screen, and the offline one has to name
 * the time history reaches. A banner that renders "the gateway says something" for all three
 * would pass a smoke test and tell a reader nothing, which is the failure this checks for.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { AuthProvider } from "../AuthContext";
import { PlantStatusBanner } from "../plant/PlantStatusBanner";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";

/** The gateway's own field names and casing, as `GatewayStatus` is serialised — camelCase,
 * because that is what `make m1-demo` greps for and what the browser receives. */
function status(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    state: "live",
    lastEventSourceTs: "2026-09-11T03:14:00",
    queueDepth: 0,
    backfillProgress: 0,
    overflowCount: 0,
    rowsWritten: 4210,
    historyAvailableFrom: "2026-09-10T18:00:00",
    clockAvailable: true,
    ...overrides,
  };
}

function answering(body: unknown, init: ResponseInit = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          headers: { "Content-Type": "application/json" },
          ...init,
        }),
      ),
    ),
  );
}

function banner() {
  return render(
    <AuthProvider>
      <PlantStatusBanner />
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "dev-token-123");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("a live gateway reads as connected", async () => {
  answering(status({ state: "live" }));
  banner();

  expect(await screen.findByText("Connected")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveAttribute("data-state", "live");
});

test("a backfilling gateway reads as backfilling, and says how far through", async () => {
  // "Backfilling" on its own is a state a reader cannot act on: the question underneath it
  // is whether the window they are about to ask about is filled in yet.
  answering(status({ state: "backfilling", backfillProgress: 0.42 }));
  banner();

  expect(await screen.findByText("Backfilling")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("42% of it read");
  expect(screen.getByRole("status")).toHaveAttribute(
    "data-state",
    "backfilling",
  );
});

test("an offline plant reads as offline and names the time history reaches", async () => {
  // §7.2 writes the banner as "plant offline, history to 03:14" — and the second half is
  // the half that matters. "Offline" alone says nothing about whether the answer on screen
  // is current, which is the only question a reader has when they see it.
  answering(
    status({ state: "disconnected", lastEventSourceTs: "2026-09-11T03:14:00" }),
  );
  banner();

  expect(await screen.findByText("Plant offline")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("2026-09-11T03:14:00");
  expect(screen.getByRole("status")).toHaveAttribute(
    "data-state",
    "disconnected",
  );
});

/** What the banner reads as for one gateway state, with nothing of it left mounted.
 *
 * Re-queried inside `waitFor` rather than captured once: the banner replaces its node when
 * it stops reading and starts reporting, so a reference taken while it was still reading is
 * a reference to something no longer on the screen.
 */
async function readingOf(state: string): Promise<string> {
  answering(status({ state }));
  const { unmount } = banner();
  await waitFor(() => {
    expect(screen.getByRole("status")).toHaveAttribute("data-state", state);
  });
  const reading = screen.getByRole("status").textContent ?? "";
  unmount();
  vi.unstubAllGlobals();
  return reading;
}

test("the three readings are three different readings", async () => {
  // The assertion the three tests above cannot make individually: that the banner is not
  // rendering one sentence with the state name substituted into it.
  const live = await readingOf("live");
  const backfilling = await readingOf("backfilling");
  const offline = await readingOf("disconnected");

  expect(new Set([live, backfilling, offline]).size).toBe(3);
});

test("an offline gateway that has read nothing does not claim there is no history", async () => {
  // `lastEventSourceTs` is the last event *this gateway process* read, so a gateway
  // restarted while the plant is down reports null over a database full of history. Saying
  // "there is no history" there would be false, and it is the sentence a reader would act on.
  answering(status({ state: "disconnected", lastEventSourceTs: null }));
  banner();

  expect(await screen.findByText("Plant offline")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    /has read no event since it started/,
  );
});

test("a gateway that cannot be reached is not reported as a plant that is off", async () => {
  // The two are opposite facts: one is about this stack and one is about the plant, and a
  // banner that collapsed them would report the wrong outage to whoever went to fix it.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("connection refused"))),
  );
  banner();

  expect(await screen.findByText("Plant status unknown")).toBeInTheDocument();
  const region = screen.getByRole("status");
  expect(region).toHaveAttribute("data-state", "unreadable");
  expect(region).toHaveTextContent(
    /says nothing about whether the plant is running/,
  );
  expect(region).not.toHaveTextContent("Plant offline");
});

test("a state this application has no reading for says so rather than guessing", async () => {
  answering(status({ state: "resynchronising" }));
  banner();

  expect(
    await screen.findByText("Unrecognised gateway state"),
  ).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveAttribute(
    "data-state",
    "unrecognised",
  );
  // The gateway's own word is still shown: this application not knowing a state is not a
  // reason to hide which state it was.
  expect(screen.getByRole("status")).toHaveTextContent("resynchronising");
});

test("a response this application cannot read is a parsing failure, not a plant state", async () => {
  // The whole reason `PlantStatus` is hand-written and parsed rather than trusted: without
  // the parse, a renamed field arrives as `undefined` and renders as an unrecognised state
  // or an absent history — both real conditions with real meanings.
  const renamed = status();
  delete renamed.queueDepth;
  answering(renamed);
  banner();

  expect(await screen.findByText("Plant status unknown")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    /queueDepth is not a number/,
  );
});

test("a queue that is not empty says the most recent minutes are not answerable", async () => {
  // §4.4: rows held at the gateway are rows the analysis service cannot see, which changes
  // how every answer on the screen should be read.
  answering(status({ queueDepth: 137 }));
  banner();

  expect(await screen.findByText("Connected")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    /137 records are queued at the gateway/,
  );
});

test("a plant with no clock phase says the handshake could not be made", async () => {
  answering(status({ clockAvailable: false }));
  banner();

  expect(await screen.findByText("Connected")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    /cannot tell catch-up from live/,
  );
});

test("the banner carries the bearer token", async () => {
  // §10.5: `/status` is an operational picture of the boundary and is not public — the
  // gateway 401s a request without one, so a banner that forgot it would render "could not
  // be read" for ever against a perfectly healthy plant.
  answering(status());
  banner();

  await screen.findByText("Connected");
  const call = vi.mocked(fetch).mock.calls[0];
  expect(call?.[0]).toBe("/api/gateway/status");
  expect(call?.[1]).toMatchObject({
    headers: { Authorization: "Bearer dev-token-123" },
  });
});

test("with no token the gateway is not asked at all", async () => {
  // The other half of the line above: §10.5 refuses an unauthenticated request, so the round
  // trip is one whose 401 is already known — and a 401 rendered in this banner would read as
  // "the gateway is unreachable", which is a claim about the boundary and not about the
  // browser that has not signed in.
  window.localStorage.clear();
  answering(status());
  banner();

  expect(await screen.findByText("Plant status not read")).toBeInTheDocument();
  expect(vi.mocked(fetch)).not.toHaveBeenCalled();
});
