/** The simulator's in-process HMI server, as this origin exposes it.
 *
 * Every path here is relative to the page. `/api/plant/*` is what nginx and the Vite dev
 * server forward, and the simulator does not know it is behind either of them — the same
 * split `useLineSnapshot` makes for the socket URL, and the reason no URL is baked into
 * the bundle at build time and the simulator configures no CORS.
 */

/** A path on the simulator's HMI server, as a URL on this origin. */
export function plantUrl(path: string): string {
  return new URL(`api/plant${path}`, window.location.href).toString();
}

/** One of §3.5's fault kinds, as `GET /faults` offers it.
 *
 * Fetched rather than written down here. `simulator.faults` is where the vocabulary
 * lives; a copy on this side would offer a parameter the plant refuses, and the operator
 * would find out by pressing the button.
 */
export interface FaultKindView {
  kind: string;
  /** In the order to ask for them: the magnitude, the scope where there is one, the ramp. */
  parameters: string[];
  /** `carrier` or `lane`, or null for a kind that applies line-wide. */
  scope: string | null;
  scope_required: boolean;
}

/** A fault the plant took, as `POST /faults` answers. The instants are simulated. */
export interface InjectedView {
  kind: string;
  at: string;
  until: string | null;
  params: Record<string, number>;
}

async function ok(response: Response): Promise<Response> {
  if (response.ok) return response;
  // The plant answers 400 with the reason it refused — a parameter the kind does not
  // take, a window that cannot happen. Carrying that text to the screen is the whole
  // point of the status: swallowing it would leave a button that does nothing.
  const detail = await response.text();
  throw new Error(detail === "" ? `HTTP ${String(response.status)}` : detail);
}

export async function fetchFaultKinds(): Promise<FaultKindView[]> {
  const response = await ok(await fetch(plantUrl("/faults")));
  return (await response.json()) as FaultKindView[];
}

/** §3.7's acknowledge button. 404 is an alarm whose operator has already been — the
 * screen is a frame behind the line, which is a race and not a failure. */
export async function acknowledgeAlarm(sequence: number): Promise<void> {
  const response = await fetch(
    plantUrl(`/alarms/${String(sequence)}/acknowledge`),
    { method: "POST" },
  );
  if (response.status === 404) return;
  await ok(response);
}

/** §3.7's fault-injection panel.
 *
 * `durationSeconds` is null for a fault nothing repairs, which is a real choice — §3.5's
 * row 3 is exactly that — so the panel says which it meant rather than leaving one of
 * the two as the quiet case.
 */
export async function injectFault(
  kind: string,
  params: Record<string, number>,
  durationSeconds: number | null,
): Promise<InjectedView> {
  const response = await ok(
    await fetch(plantUrl("/faults"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind,
        params,
        duration_seconds: durationSeconds,
      }),
    }),
  );
  return (await response.json()) as InjectedView;
}
