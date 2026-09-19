/** The frame every citation opens into, whatever kind it is.
 *
 * Three outcomes and no fourth. A panel that is still opening says so, a panel that failed
 * says why and whether the service looked, and a panel that opened shows the record. There
 * is no branch that renders nothing — §7.2's claim is that a citation opens, and a blank
 * region is the way that claim fails quietly.
 */
import type { ReactNode } from "react";

import type { Resolution } from "./useResolution";

export function CitationPanel<T>({
  what,
  resolution,
  children,
}: {
  /** How this citation names itself in a failure line — `stop stop-2026…`, `SOP-01`. */
  what: string;
  resolution: Resolution<T>;
  children: (value: T) => ReactNode;
}) {
  if (resolution.state === "opening") {
    return (
      <div
        data-testid="evidence-panel"
        data-outcome="opening"
        className="evidence"
      >
        Opening {what}…
      </div>
    );
  }

  if (resolution.state === "failed") {
    return (
      <div
        data-testid="evidence-panel"
        data-outcome={resolution.missing ? "not-found" : "failed"}
        className="evidence evidence--error"
      >
        {resolution.missing
          ? `Could not open ${what}: the service looked and found nothing. ${resolution.reason}`
          : `Could not open ${what}: ${resolution.reason}`}
      </div>
    );
  }

  return (
    <div data-testid="evidence-panel" data-outcome="open" className="evidence">
      {children(resolution.value)}
    </div>
  );
}

/** A citation that resolved onto a record the *panel* can read but the reader cannot use.
 *
 * The window-scoped kinds need this: `/inspection/patterns` answers 200 with a whole report
 * and the cell a `pattern` citation names may not be in it. That is not a 404 and it is not
 * an error either — it is a resolution that came back without the referent, and saying so
 * is the difference between "this pattern is not there" and a panel that looks empty.
 */
export function NotInTheAnswer({
  what,
  detail,
}: {
  what: string;
  detail: string;
}) {
  return (
    <p data-testid="citation-missing" className="evidence__missing">
      {what} is not in what came back: {detail}
    </p>
  );
}
