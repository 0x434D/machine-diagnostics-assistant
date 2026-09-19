/** Which question and answer the citations on the screen belong to.
 *
 * Eight of §7.3's ten kinds name something the database holds once — a stop, a part, a
 * procedure — and resolve against the analysis service with nothing else needed. A `chart`
 * does not: §7.4 makes its referent *a tool call this run made*, and a tool call id is
 * meaningful only inside the exchange that made it. `POST /ask` names that exchange in its
 * first event, before any step has run, precisely so a client can reach the trace
 * afterwards; this carries it from the view that received it down to the chip that needs it.
 *
 * **Not a field on the citation.** §7.3 writes the chart citation as
 * `{ kind, chart_type, source, options }` and stamping a session id into it would be
 * inventing contract surface the spec does not name — and putting an address into an object
 * whose whole purpose is to be addressable by its own contents. The exchange is a property
 * of the page, so it lives on the page.
 */
import { createContext, use, type ReactNode } from "react";

export interface Exchange {
  /** `agent.sessions.id`. */
  readonly sessionId: string;
  /** The question's row in `agent.messages` — what §7.2's trace is addressed by. */
  readonly seq: number;
}

const ExchangeContext = createContext<Exchange | null>(null);

export function ExchangeProvider({
  exchange,
  children,
}: {
  exchange: Exchange;
  children: ReactNode;
}) {
  return <ExchangeContext value={exchange}>{children}</ExchangeContext>;
}

/** The exchange these citations came from, or `null` outside one.
 *
 * Null is a real answer and not an oversight: a citation rendered outside an answer — in a
 * story, in a test, in a view that lists evidence on its own — has no trace to reach, and
 * the chart renderer says so rather than fetching a trace belonging to somebody else.
 */
export function useExchange(): Exchange | null {
  return use(ExchangeContext);
}
