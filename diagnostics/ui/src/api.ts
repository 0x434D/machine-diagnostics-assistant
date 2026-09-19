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
import type {
  CitationWindow,
  MachineAgentAnswerObject63,
} from "./generated/answer";
import type { MachineAgentReasoningTrace72 } from "./generated/trace";
import type { Exchange } from "./citations/exchange";

export type Answer = MachineAgentAnswerObject63;
export type Trace = MachineAgentReasoningTrace72;
export type Part = components["schemas"]["Part"];
export type Alarm = components["schemas"]["Alarm"];
export type ComponentAssembly = components["schemas"]["ComponentAssembly"];
export type KnowledgeDocument = components["schemas"]["KnowledgeDocument"];
export type LotParts = components["schemas"]["LotParts"];
export type PatternReport = components["schemas"]["PatternReport"];
export type PatternValue = components["schemas"]["PatternValue"];
export type SignalTrend = components["schemas"]["SignalTrend"];
export type StopDetail = components["schemas"]["StopDetail"];

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

/** The service answered, and what was asked for is not there.
 *
 * Its own class because it is the only failure that is a fact about the *data* rather than
 * about the request or the network, and §6.5 rests on that difference: "no such id" and
 * "nothing to show" must never render as the same sentence. §7.2's citation panel is where
 * a reader acts on it — a cited stop that 404s is a citation the agent should not have
 * shipped, and a cited stop that timed out is nothing of the kind.
 */
export class NotFoundError extends Error {}

/** The request never reached a service at all.
 *
 * Kept apart from every status-carrying failure for the reason above, read the other way:
 * a 404 says the service looked and found nothing, and this says nobody looked. Rendering
 * the two alike would let an offline browser report every citation in an answer as
 * unresolvable, which is a far stronger claim than the truth.
 */
export class UnreachableError extends Error {}

/** What the reader should be told for a failure from `ask` or any of the fetches below.
 *
 * Each class carries its own message already — 401 and 403 from `refuseIfDenied`, 404 from
 * the service's own `detail`, unreachable from the browser — so this chooses between them
 * rather than inventing a sentence. Anything else has no more specific fact to add than its
 * own message.
 */
export function describeFailure(reason: unknown): string {
  if (reason instanceof NotFoundError || reason instanceof UnreachableError) {
    return reason.message;
  }
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
  // An `Error`'s own words, without the class name `String()` would prefix them with: the
  // panel already says what failed to open, and "Error: 500 Internal Server Error" spends a
  // line telling a reader that a failure was a failure.
  return reason instanceof Error ? reason.message : String(reason);
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

/** One authenticated GET, with every failure it can have turned into a named one.
 *
 * Nine citation kinds resolve through here (§7.3). Written once rather than once per kind
 * because the branch that matters — 404 apart from everything else — is the branch a
 * renderer written in a hurry collapses, and nine chances to collapse it is nine chances
 * for an unresolvable citation to render as an empty panel.
 */
async function request(url: string, token: string | null): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, { headers: authHeaders(token) });
  } catch (reason: unknown) {
    // `fetch` rejects only when the request never completed: no connection, no DNS, the
    // browser offline, the load aborted. That is a specific condition with a specific
    // recovery — the rendered "could not be reached" state — and nothing further along is
    // caught here, so a failure while *reading* the response still propagates.
    throw new UnreachableError(
      `the analysis service could not be reached (${String(reason)})`,
    );
  }
  await refuseIfDenied(response);
  if (response.status === 404) {
    throw new NotFoundError(await detailOf(response));
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response;
}

/** The analysis service's URL for a path and its query, same-origin. */
function analysisUrl(path: string, query: Record<string, string> = {}): string {
  const search = new URLSearchParams(query).toString();
  return `${ANALYSIS}${path}${search === "" ? "" : `?${search}`}`;
}

async function getAnalysis<T>(
  path: string,
  token: string | null,
  query: Record<string, string> = {},
): Promise<T> {
  const response = await request(analysisUrl(path, query), token);
  return (await response.json()) as T;
}

export async function fetchPart(
  serial: string,
  token: string | null,
): Promise<Part> {
  return await getAnalysis<Part>(`/parts/${encodeURIComponent(serial)}`, token);
}

/** §5.3's `/stops/{id}`: the stop, the state timeline around it, and §5.4's derivation. */
export async function fetchStop(
  identifier: string,
  token: string | null,
): Promise<StopDetail> {
  return await getAnalysis<StopDetail>(
    `/stops/${encodeURIComponent(identifier)}`,
    token,
  );
}

/** §5.3's `/alarms/{id}`, added at M6 so an `alarm` citation has somewhere to open. */
export async function fetchAlarm(
  identifier: string,
  token: string | null,
): Promise<Alarm> {
  return await getAnalysis<Alarm>(
    `/alarms/${encodeURIComponent(identifier)}`,
    token,
  );
}

/** §6.2's document behind a cited procedure — the SOP text itself, which is the one §7.2
 * singles out as the thing a citation has to be able to open. */
export async function fetchKnowledgeDocument(
  documentId: string,
  token: string | null,
): Promise<KnowledgeDocument> {
  return await getAnalysis<KnowledgeDocument>(
    `/knowledge/${encodeURIComponent(documentId)}`,
    token,
  );
}

/** §5.3's single-component recall. A `serial` citation is a *component* serial and not an
 * assembly one — RULING M4-R5, and `agent.citations.resolves` verifies it against this same
 * endpoint, so opening it anywhere else could fail on a citation that was checked and kept. */
export async function fetchComponentAssembly(
  serial: string,
  token: string | null,
): Promise<ComponentAssembly> {
  return await getAnalysis<ComponentAssembly>(
    `/components/${encodeURIComponent(serial)}/assembly`,
    token,
  );
}

/** Which assemblies carry a component from this lot, split by where each of them went. */
export async function fetchLotParts(
  lotCode: string,
  token: string | null,
): Promise<LotParts> {
  return await getAnalysis<LotParts>(
    `/lots/${encodeURIComponent(lotCode)}/parts`,
    token,
  );
}

/** The half-open `[from, to)` a §5.3 endpoint takes, out of the window a citation carries.
 *
 * §7.3 gives the `pattern` and `signal` citations a window and the agent writes it from the
 * interval the answer was computed over (§6.1 step 2), so this is the only interval either
 * panel may open over. A recent default would show real rows from the database under a
 * sentence they do not support — which reads more authoritatively than a wrong sentence, and
 * is §7.4's stated reason for the whole design.
 */
function windowQuery(window: CitationWindow): Record<string, string> {
  return { from: window.from_ts, to: window.to_ts };
}

/** §5.5's pattern report over the window the claim was made in. */
export async function fetchPatterns(
  window: CitationWindow,
  token: string | null,
): Promise<PatternReport> {
  return await getAnalysis<PatternReport>(
    "/inspection/patterns",
    token,
    windowQuery(window),
  );
}

/** §5.3's `/signals/trend`, bucketed by the hour.
 *
 * Bucketed rather than raw because a citation opens into a panel and not a chart until
 * Task 5: at §3.1's takt an hour of `raw` is 600 samples and a shift of it is a scroll
 * rather than a reading, where hour buckets are one row each. The panel names the
 * aggregation, because a bucket mean is not a measurement and must not read as one.
 */
export async function fetchSignalTrend(
  station: string,
  signal: string,
  window: CitationWindow,
  token: string | null,
): Promise<SignalTrend> {
  return await getAnalysis<SignalTrend>("/signals/trend", token, {
    station,
    signal,
    agg: "hour",
    ...windowQuery(window),
  });
}

/** §7.2's reasoning trace for one answered exchange.
 *
 * The agent rather than the analysis service, and under the reader's own identity: a session
 * belongs to whoever opened it, and the endpoint refuses a trace that is not the caller's
 * (§10.5). §7.4's charts are drawn from the tool results inside it — a chart references a
 * call by id, and the stored result is the copy the model reasoned over. Re-querying the
 * analysis service instead would be a second read, minutes later, which can disagree with
 * the first and would put a chart under an answer that was never based on it.
 */
export async function fetchTrace(
  exchange: Exchange,
  token: string | null,
): Promise<Trace> {
  const path =
    `${AGENT}/sessions/${encodeURIComponent(exchange.sessionId)}` +
    `/messages/${encodeURIComponent(String(exchange.seq))}/trace`;
  return (await (await request(path, token)).json()) as Trace;
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
  return await (await request(path, token)).blob();
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
  report: {
    progress: (message: string) => void;
    /** The `session` event, which arrives **before** any step has run. Reported as it
     * lands rather than returned with the answer, because §7.2 wants the trace reachable
     * for an answer that never arrives — and an exchange handed over at the end would be
     * lost by exactly the run that most needs looking into. */
    exchange: (exchange: Exchange) => void;
  },
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
      report.progress((JSON.parse(data) as { message: string }).message);
    } else if (event === "session") {
      const named = JSON.parse(data) as { session_id: string; seq: number };
      report.exchange({ sessionId: named.session_id, seq: named.seq });
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
