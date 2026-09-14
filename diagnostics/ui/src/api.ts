/** The two services, as the browser sees them.
 *
 * Types come from `contracts/` via `pnpm generate` — never hand-written, because a
 * hand-written type asserts the shape of someone else's response with full confidence and
 * no way to be wrong out loud.
 */
import type { components } from "./generated/analysis";
import type { MachineAgentAnswerObject63 } from "./generated/answer";

export type Answer = MachineAgentAnswerObject63;
export type Part = components["schemas"]["Part"];

/** Same-origin, and forwarded by the dev server or nginx. See vite.config.ts. */
const AGENT = "/api/agent";
const ANALYSIS = "/api/analysis";

export async function fetchPart(serial: string): Promise<Part> {
  const response = await fetch(
    `${ANALYSIS}/parts/${encodeURIComponent(serial)}`,
  );
  if (!response.ok) {
    throw new Error(`${serial}: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as Part;
}

/** §3.4: a good part has no image, and that is not a missing value.
 *
 * `inspection` is null for a part that has not reached S3 — the ordinary state of every
 * serial between the press and the camera — which is a third case and not an image. */
export function imageUrl(part: Part): string | null {
  const url = part.inspection?.image_url ?? null;
  return url === null ? null : `${ANALYSIS}${url}`;
}

/**
 * Asks, reporting each step as it arrives and resolving with §6.3's answer object.
 *
 * `EventSource` cannot do this: it is GET-only, and the question is a POST body. Reading
 * the stream off `fetch` is the remaining option, so the small SSE parser below is ours.
 */
export async function ask(
  question: string,
  onProgress: (message: string) => void,
): Promise<Answer> {
  // No AbortSignal parameter. There is nothing to cancel: the Ask button is disabled for
  // the duration, so a second stream cannot start while the first is running, and a
  // parameter no caller supplies is a guess about a future that has not arrived.
  const response = await fetch(`${AGENT}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok || response.body === null) {
    throw new Error(`ask failed: ${response.status} ${response.statusText}`);
  }

  let answer: Answer | null = null;
  for await (const [event, data] of events(response.body)) {
    if (event === "progress") {
      onProgress((JSON.parse(data) as { message: string }).message);
    } else if (event === "answer") {
      answer = JSON.parse(data) as Answer;
    }
  }

  if (answer === null) {
    // Loud on purpose. A stream that ended without an answer is a failure, and showing
    // the progress lines and nothing else would read like a result.
    throw new Error("the stream ended without an answer");
  }
  return answer;
}

async function* events(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<[string, string]> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    // A stream is read one chunk at a time: Promise.all has nothing to parallelise here,
    // and the next read does not exist until this one resolves.
    // oxlint-disable-next-line no-await-in-loop
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const parsed = parseBlock(block);
      if (parsed !== null) yield parsed;
      split = buffer.indexOf("\n\n");
    }
  }
}

function parseBlock(block: string): [string, string] | null {
  let event = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) data = line.slice("data:".length).trim();
  }
  return event === "" ? null : [event, data];
}
