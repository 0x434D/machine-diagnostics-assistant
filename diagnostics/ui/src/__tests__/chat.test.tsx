/** The question box, the steps, and the answer — over a real server-sent-event body. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { Chat } from "../Chat";
import type { Answer } from "../api";

const ANSWER: Answer = {
  findings: [
    {
      statement: "600 parts were inspected, of which 30 were rejected.",
      basis: "measured",
      citations: [{ kind: "part", id: "A-00000007" }],
    },
  ],
  answer_markdown: "600 parts were inspected, of which 30 were rejected.",
  method: {
    tools_called: ["inspection_stats"],
    budget_used: 1,
    provider: "scripted",
  },
  caveats: [],
};

function sse(blocks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      // One chunk per event, and the split deliberately does not align with the frame
      // boundary: a parser that only works when each read is a whole event is a parser
      // that works in a test and fails on a real socket.
      for (const block of blocks) controller.enqueue(encoder.encode(block));
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the steps arrive before the answer, and the answer carries its citation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) =>
      Promise.resolve(
        input.startsWith("/api/agent")
          ? sse([
              'event: progress\ndata: {"message":"calling inspection_stats"}\n',
              "\nevent: answer\ndata: " + JSON.stringify(ANSWER) + "\n\n",
            ])
          : new Response("{}"),
      ),
    ),
  );

  render(<Chat />);
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(
    await screen.findByText("calling inspection_stats"),
  ).toBeInTheDocument();
  await waitFor(() => {
    expect(
      screen.getByText("600 parts were inspected, of which 30 were rejected."),
    ).toBeInTheDocument();
  });
  expect(
    screen.getByRole("button", { name: "A-00000007" }),
  ).toBeInTheDocument();
});

test("the reasoning trace names the provider that answered", async () => {
  // A scripted answer and a model's answer are not the same claim, and §7.2 keeps the
  // difference where a reader can see it rather than only in the audit trail.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        sse(["event: answer\ndata: " + JSON.stringify(ANSWER) + "\n\n"]),
      ),
    ),
  );

  render(<Chat />);
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText("scripted")).toBeInTheDocument();
});

test("a caveat renders as emphasis, not as literal underscores", async () => {
  // The composer wraps caveats in _underscores_, and every scripted answer carries one, so
  // getting this wrong put markdown syntax in front of the reader on every single answer.
  const withCaveat: Answer = {
    ...ANSWER,
    answer_markdown:
      "600 parts were inspected.\n\n_The window contains 1 ingest gap._",
    caveats: ["The window contains 1 ingest gap."],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        sse(["event: answer\ndata: " + JSON.stringify(withCaveat) + "\n\n"]),
      ),
    ),
  );

  render(<Chat />);
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  const caveat = await screen.findByText("The window contains 1 ingest gap.");
  expect(caveat.tagName).toBe("EM");
  // Scoped to the prose: the trace legitimately shows `inspection_stats`, so a document-wide
  // search for an underscore fails on the tool name rather than on the markdown.
  expect(caveat.closest("article")).not.toHaveTextContent("_The window");
});

test("a stream that ends without an answer is reported, not left blank", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        sse(['event: progress\ndata: {"message":"working"}\n\n']),
      ),
    ),
  );

  render(<Chat />);
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    /ended without an answer/,
  );
});
