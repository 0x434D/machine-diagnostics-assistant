/** The services, as the browser sees them.
 *
 * Types come from `contracts/` via `pnpm generate` — never hand-written, because a
 * hand-written type asserts the shape of someone else's response with full confidence and
 * no way to be wrong out loud. The issuer's two fields below are the exception and are
 * hand-written on purpose: there is no entry for the development issuer in `contracts/`,
 * because it is the one service a production deployment replaces rather than reimplements
 * (§10.5), and generating a contract for something meant to be thrown away would assert
 * that it is part of the system's surface.
 */
import type { components } from "./generated/analysis";
import type { MachineAgentAnswerObject63 } from "./generated/answer";

export type Answer = MachineAgentAnswerObject63;
export type Part = components["schemas"]["Part"];

/** Same-origin, and forwarded by the dev server or nginx. See vite.config.ts. */
const AGENT = "/api/agent";
const ANALYSIS = "/api/analysis";
/** The development issuer publishes no port of its own — it sits on the internal diag-net
 * and nginx forwards to it there — so this origin is the only way a browser reaches it. */
const ISSUER = "/api/issuer";

/** No token presented, or the one presented is invalid, expired, or for another issuer.
 * Every diagnostics endpoint answers this the same way (§10.5) and says nothing more --
 * see `auth.tokens.verify`'s docstring for why the reason stays server-side. */
export class UnauthorizedError extends Error {}

/** Authenticated, but the role on the token is not the one this action requires. Kept
 * distinct from `UnauthorizedError` because the server deliberately makes the same
 * distinction (§14) and collapsing them back into one message on the client would spend it. */
export class ForbiddenError extends Error {}

/** What the reader should be told for a failure from `ask` or `fetchPart`.
 *
 * 401 and 403 carry their own message already (`refuseIfDenied` below); anything else has
 * no more specific fact to add than its own message.
 */
export function describeFailure(reason: unknown): string {
  if (reason instanceof UnauthorizedError) {
    // Reached while a token *is* held — the login screen stands in front of everything
    // else — so this is the server refusing the one presented: expired, or minted by an
    // issuer this deployment does not trust. Signing out and back in is the fix for both,
    // and which of the two it was stays server-side (§10.5).
    return "Not signed in: this token was refused. Sign out and sign in again.";
  }
  if (reason instanceof ForbiddenError) {
    return reason.message;
  }
  return String(reason);
}

function authHeaders(token: string | null): HeadersInit {
  return token === null || token === ""
    ? {}
    : { Authorization: `Bearer ${token}` };
}

function isDetailBody(value: unknown): value is { detail: string } {
  if (typeof value !== "object" || value === null) return false;
  return typeof (value as { detail?: unknown }).detail === "string";
}

/** The server's `{"detail": "..."}` body for a 401 or 403 (§10.5), or the status line if
 * whatever answered was not JSON -- a refusal is still a refusal even if something in front
 * of the service (a proxy, a gateway timeout page) answered instead of it. */
async function detailOf(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return response.statusText;
  const body: unknown = await response.json();
  return isDetailBody(body) ? body.detail : response.statusText;
}

/** Turns the two refusals §10.5 makes distinguishable into the errors above, so a caller
 * can tell "not signed in" from "signed in as the wrong role" without re-reading the
 * status code itself. Every other status is left to the caller, unchanged. */
async function refuseIfDenied(response: Response): Promise<void> {
  if (response.status !== 401 && response.status !== 403) return;
  const message = await detailOf(response);
  throw response.status === 401
    ? new UnauthorizedError(message)
    : new ForbiddenError(message);
}

/** The login: credentials to the development issuer, a bearer token back.
 *
 * The only request in this file that carries no token, and the only one that may: it is
 * where a token comes from. Everything else in the application is behind the screen that
 * calls this.
 *
 * A refusal throws with **the issuer's own words**, not with a sentence invented here. Its
 * two refusals say different things on purpose — "these credentials are not valid" tells the
 * reader to retype, and the 503 that names `ISSUER_PRIVATE_KEY` tells them nothing they type
 * will work until somebody configures the stack — and rewriting either into one client-side
 * "sign-in failed" would throw the difference away. Note the credentials refusal is
 * deliberately uniform on the server: it never says which half was wrong (§10.5).
 */
export async function signIn(
  username: string,
  password: string,
): Promise<string> {
  const response = await fetch(`${ISSUER}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error(await detailOf(response));
  }
  const body = (await response.json()) as { access_token: string };
  return body.access_token;
}

export async function fetchPart(
  serial: string,
  token: string | null,
): Promise<Part> {
  const response = await fetch(
    `${ANALYSIS}/parts/${encodeURIComponent(serial)}`,
    { headers: authHeaders(token) },
  );
  await refuseIfDenied(response);
  if (!response.ok) {
    throw new Error(`${serial}: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as Part;
}

/** §3.4: a good part has no image, and that is not a missing value.
 *
 * `inspection` is null for a part that has not reached S3 — the ordinary state of every
 * serial between the press and the camera — which is a third case and not an image. */
export function imagePath(part: Part): string | null {
  const url = part.inspection?.image_url ?? null;
  return url === null ? null : `${ANALYSIS}${url}`;
}

/** The reject image, as bytes, fetched under the reader's own identity.
 *
 * **This exists because a browser cannot put an `Authorization` header on an `<img>`.**
 * `GET /parts/{serial}/image` refuses an unauthenticated request like every other endpoint
 * since M5, and the element the panel used to render pointed straight at it — so the image
 * a quality engineer opens a citation *for* silently failed to load. §7.2: a citation you
 * cannot open is barely a citation, and that applies hardest to the evidence itself.
 *
 * The alternative was a short-lived signed query parameter on the endpoint, and it was not
 * taken. It would be a second credential shape — minted somewhere, verified somewhere,
 * expiring on its own schedule — beside the one `auth.tokens.verify` implements, which is
 * the duplication the `auth` package exists to prevent; and it would put a credential in a
 * URL, where browser history, `Referer` and every access log downstream keep a copy. This
 * costs one `revokeObjectURL` in the caller instead, which is local and checkable.
 */
export async function fetchImage(
  path: string,
  token: string | null,
): Promise<Blob> {
  const response = await fetch(path, { headers: authHeaders(token) });
  await refuseIfDenied(response);
  if (!response.ok) {
    throw new Error(`${path}: ${response.status} ${response.statusText}`);
  }
  return await response.blob();
}

/**
 * Asks, reporting each step as it arrives and resolving with §6.3's answer object.
 *
 * `EventSource` cannot do this: it is GET-only, and the question is a POST body. Reading
 * the stream off `fetch` is the remaining option, so the small SSE parser below is ours.
 */
export async function ask(
  question: string,
  token: string | null,
  onProgress: (message: string) => void,
): Promise<Answer> {
  // No AbortSignal parameter. There is nothing to cancel: the Ask button is disabled for
  // the duration, so a second stream cannot start while the first is running, and a
  // parameter no caller supplies is a guess about a future that has not arrived.
  const response = await fetch(`${AGENT}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(token) },
    body: JSON.stringify({ question }),
  });
  await refuseIfDenied(response);
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
