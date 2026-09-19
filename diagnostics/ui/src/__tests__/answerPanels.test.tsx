/** What sits under an answer: §6.5's contradiction, §7.2's recorded trace, §7.2's feedback.
 *
 * All three are driven through `Chat` over a real server-sent-event body rather than by
 * rendering the three components directly, because the thing worth pinning is that they get
 * the exchange the stream named — the address both the trace and the feedback hang off, and
 * the one part of this that no component can be given by a test that hands it in.
 */
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { AuthProvider } from "../AuthContext";
import { Chat } from "../Chat";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type { Answer, Trace } from "../api";

const SESSION = "0c1f0a3e-3b6f-4a2f-9a26-0ba8b6b2a9f1";
const SEQ = 4;

const ANSWER: Answer = {
  findings: [
    {
      statement: "S2 held for 214 s on a joining-force fault.",
      basis: "measured",
      citations: [],
    },
  ],
  answer_markdown: "S2 held for 214 s on a joining-force fault.",
  method: {
    tools_called: ["list_stops"],
    budget_used: 2,
    provider: "scripted",
  },
  caveats: [],
};

const TRACE: Trace = {
  sops_loaded: ["SOP-03"],
  tool_calls: [
    {
      id: "call_0191",
      name: "list_stops",
      arguments: { station: "S2", from: "2026-09-11T22:00:00Z" },
      result: { stops: [] },
      duration_ms: 31.5,
      failed: false,
    },
    {
      id: "call_0192",
      name: "signal_trend",
      arguments: { station: "S2", signal: "joining_force" },
      result: { detail: "no such signal" },
      duration_ms: 4,
      failed: true,
    },
  ],
  budget: { tool_turns: 2, tool_turns_limit: 8 },
  timings: { total_ms: 812, model_ms: 640, tools_ms: 35.5 },
};

function sse(blocks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const block of blocks) controller.enqueue(encoder.encode(block));
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The two endpoints Task 2 built, faked with the one behaviour that matters: the feedback
 * upsert `COALESCE`s per column, so a submission that answers one question leaves the other
 * as it found it and the response carries both. A fake that echoed the request instead would
 * let a panel rendering its own optimism pass. */
function server({
  answer = ANSWER,
  trace = json(TRACE),
}: { answer?: Answer; trace?: Response } = {}) {
  const stored: Record<string, unknown> = {
    useful: null,
    matched_reality: null,
    comment: null,
  };

  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      if (input.endsWith("/ask")) {
        return Promise.resolve(
          sse([
            `event: session\ndata: {"session_id":"${SESSION}","seq":${String(SEQ)}}\n\n`,
            `event: answer\ndata: ${JSON.stringify(answer)}\n\n`,
          ]),
        );
      }
      if (input.endsWith("/trace")) return Promise.resolve(trace);
      if (input.endsWith("/feedback")) {
        const given = JSON.parse(String(init?.body)) as Record<string, unknown>;
        for (const [name, value] of Object.entries(given)) {
          if (value !== null && value !== undefined) stored[name] = value;
        }
        return Promise.resolve(json({ ...stored }));
      }
      return Promise.resolve(json({}));
    }),
  );
  return stored;
}

async function ask() {
  render(
    <AuthProvider>
      <Chat />
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  await screen.findByText(ANSWER.answer_markdown);
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "dev-token-123");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

// --- §6.5's contradiction ----------------------------------------------------------------

test("an answer carrying a contradiction states both sides and the reasoning", async () => {
  // §6.5: the UI renders this prominently, and it is two claims a person has to choose
  // between. One side shown without the other is a second unexplained verdict.
  server({
    answer: {
      ...ANSWER,
      contradiction: {
        derived_root: "S2",
        agent_root: "S1",
        reasoning:
          "An operator cleared S2 by hand mid-stop, so its Held window is shorter than the fault that caused it.",
      },
    },
  });
  await ask();

  const banner = screen.getByRole("region", {
    name: "The agent disagrees with the computed propagation",
  });
  expect(banner).toHaveTextContent("Computed root");
  expect(banner).toHaveTextContent("S2");
  expect(banner).toHaveTextContent("The agent's root");
  expect(banner).toHaveTextContent("S1");
  expect(banner).toHaveTextContent(/cleared S2 by hand mid-stop/);
});

test("an answer without a contradiction shows no banner", async () => {
  // The other half, and the one a banner rendered from a truthy check on an empty object
  // would fail: a standing disagreement notice under every answer would teach a reader to
  // ignore it by the time a real one arrived.
  server();
  await ask();

  expect(
    screen.queryByRole("region", {
      name: "The agent disagrees with the computed propagation",
    }),
  ).not.toBeInTheDocument();
});

// --- §7.2's reasoning trace --------------------------------------------------------------

test("the trace renders the tool calls the endpoint returned, with their arguments", async () => {
  // §7.2's whole claim for this panel is that "did it follow the method" becomes checkable
  // by eye — and `list_stops` without its arguments says nothing about which station or
  // which window was asked for.
  server();
  await ask();

  await screen.findByText("list_stops");
  const trace = screen.getByText("list_stops").closest("li");
  expect(trace).toHaveTextContent(
    '{"station":"S2","from":"2026-09-11T22:00:00Z"}',
  );
  expect(trace).toHaveTextContent("31.5 ms");
  expect(trace).toHaveTextContent("call_0191");

  expect(screen.getByText("SOP-03")).toBeInTheDocument();
  expect(screen.getByText(/2 of 8 tool turns/)).toBeInTheDocument();
  expect(
    screen.getByText(/812 ms total · 640 ms model · 35.5 ms tools/),
  ).toBeInTheDocument();
});

test("a tool call that failed is shown as one", async () => {
  // §6.8 returns a tool error to the model as a tool result, so a failed call is an ordinary
  // thing to find inside a successful run — and it is exactly the thing an answer that does
  // not mention it would hide.
  server();
  await ask();

  const failed = (await screen.findByText("signal_trend")).closest("li");
  expect(failed).toHaveAttribute("data-failed", "true");
  expect(failed).toHaveTextContent("failed");
});

test("a run that recorded no trace says which of the three nothings it is", async () => {
  // Task 2 makes three 404s distinguishable here: no such session, no such message, and a
  // message whose run never finished. Collapsing them into "no trace" throws away the only
  // thing that says which happened.
  server({
    trace: json({ detail: "message 4 has no recorded trace" }, 404),
  });
  await ask();

  expect(
    await screen.findByText(/message 4 has no recorded trace/),
  ).toBeInTheDocument();
});

test("the trace is addressed by the exchange the stream named", async () => {
  server();
  await ask();

  await screen.findByText("list_stops");
  const asked = vi
    .mocked(fetch)
    .mock.calls.map((call) => String(call[0]))
    .find((url) => url.endsWith("/trace"));
  expect(asked).toBe(
    `/api/agent/sessions/${SESSION}/messages/${String(SEQ)}/trace`,
  );
});

// --- §7.2's feedback ---------------------------------------------------------------------

test("answering one question leaves the other answerable and does not erase it", async () => {
  // The property Task 2 built the upsert for, checked from the screen: the operator says the
  // answer was useful now, goes to the machine, and comes back an hour later to say whether
  // it matched. If the second submission cleared the first, the more valuable of the two
  // signals would cost the other one every time.
  const stored = server();
  await ask();

  const useful = screen.getByRole("group", { name: "Was this useful?" });
  fireEvent.click(within(useful).getByRole("button", { name: "Yes" }));
  await waitFor(() => {
    expect(useful).toHaveTextContent("Recorded: yes");
  });

  const matched = screen.getByRole("group", {
    name: "Did this match what you actually found?",
  });
  fireEvent.click(within(matched).getByRole("button", { name: "No" }));
  await waitFor(() => {
    expect(matched).toHaveTextContent("Recorded: no");
  });

  // Both on screen, and both in what the server holds — the panel renders the response and
  // not what it sent, so these two assertions are not the same assertion twice.
  expect(useful).toHaveTextContent("Recorded: yes");
  expect(stored).toMatchObject({ useful: true, matched_reality: false });
});

test("each submission carries only the question it answers", async () => {
  // The client half of the same property. A form that posted both fields would send `null`
  // for the unanswered one, and only the server's COALESCE would be standing between that
  // and an erased answer.
  server();
  await ask();

  const matched = screen.getByRole("group", {
    name: "Did this match what you actually found?",
  });
  fireEvent.click(within(matched).getByRole("button", { name: "Yes" }));
  await waitFor(() => {
    expect(matched).toHaveTextContent("Recorded: yes");
  });

  const sent = vi
    .mocked(fetch)
    .mock.calls.find((call) => String(call[0]).endsWith("/feedback"));
  expect(JSON.parse(String(sent?.[1]?.body))).toEqual({
    matched_reality: true,
  });
});

test("an unanswered question says it is unanswered rather than showing a default", async () => {
  // `null` is *not answered*, which is a different claim from *no* — the column is nullable
  // for exactly that reason, and a panel that rendered the two alike would report an opinion
  // nobody gave.
  server();
  await ask();

  const useful = screen.getByRole("group", { name: "Was this useful?" });
  expect(useful).toHaveTextContent("Not answered");
  expect(within(useful).getByRole("button", { name: "Yes" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

test("feedback that was not recorded says so", async () => {
  // Feedback that silently failed is worse than feedback nobody gave: the person who gave it
  // believes the system now knows.
  server();
  await ask();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(json({ detail: "no session" }, 404))),
  );

  const useful = screen.getByRole("group", { name: "Was this useful?" });
  fireEvent.click(within(useful).getByRole("button", { name: "Yes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    /Feedback was not recorded/,
  );
  expect(useful).toHaveTextContent("Not answered");
});
