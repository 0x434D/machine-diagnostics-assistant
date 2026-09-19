/** What an interval's coverage actually says, in one place.
 *
 * §4.4: *without gap markers, missing data is indistinguishable from a quiet machine.* A
 * window that holds no stop is **three** different situations and the service distinguishes
 * all three — the line was quiet, ingest was down, or part of the interval is trustworthy
 * and part is not — so a screen that renders them alike undoes the distinction the
 * `Coverage` object exists to carry. An empty chart is the worst of the three renderings,
 * because an empty chart reads as "nothing happened".
 *
 * The third fact is `observed.events`, and it is the one the gap rows cannot supply: a
 * window with no gaps and no rows is a quiet line, a window with no rows because a gap
 * covers all of it is a blackout, and the two are the same empty answer until something
 * counts what is there.
 *
 * `CoverageNote` is the rendering of all that, in one place for the same reason the sentence
 * is: the stop list, one stop, and a containment scope are three readings of an interval,
 * and three copies of "here is what was and was not observed" is three chances for one of
 * them to say less than the others.
 */
import type { Coverage } from "./api";

export type CoverageKind = "covered" | "quiet" | "partial" | "blackout";

export interface CoverageVerdict {
  readonly kind: CoverageKind;
  /** What the reader is told. Written here rather than at each call site so the timeline,
   * the stop panel and the pattern panel cannot describe one object three ways. */
  readonly sentence: string;
}

function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(1)} %`;
}

export function coverageVerdict(coverage: Coverage): CoverageVerdict {
  if (coverage.fully_covered) {
    return coverage.observed.events === 0
      ? {
          kind: "quiet",
          // No gap and no rows: the gateway was up and the line recorded nothing. That is
          // an answer — a quiet line — and it is the one an empty chart would have been
          // mistaken for.
          sentence:
            "complete — no ingest gap over this interval, and nothing at all was " +
            "recorded in it: the line was quiet, rather than unobserved.",
        }
      : {
          kind: "covered",
          sentence: `complete — no ingest gap over this interval, and ${String(coverage.observed.events)} event(s) recorded in it.`,
        };
  }

  if (coverage.covered_fraction <= 0) {
    return {
      kind: "blackout",
      sentence:
        "no data at all: ingest was down for the whole of this interval, so an absence " +
        "here is the gateway's and says nothing about the line.",
    };
  }

  return {
    kind: "partial",
    sentence:
      `${percent(coverage.covered_fraction)} covered, ` +
      `${String(coverage.gaps.length)} ingest gap(s): part of this interval is trustworthy ` +
      "and part is not, and an absence inside a gap may be the gateway's rather than the line's.",
  };
}

/** What was and was not observed over an interval, said where the reader is acting on it.
 *
 * `what` names the interval in the reader's terms — *this window*, *the stop itself* — so
 * the sentence reads as a statement about the thing on screen rather than as a status line.
 * The gaps are listed under it with their instants and the service's reason, because
 * "66.7 % covered" tells a reader that part of the interval is untrustworthy and not
 * **which** part, and which part is the half they can act on.
 */
export function CoverageNote({
  what,
  coverage,
}: {
  what: string;
  coverage: Coverage;
}) {
  return (
    <>
      <p className="evidence__note">
        Coverage over {what}: {coverageVerdict(coverage).sentence}
      </p>
      {coverage.gaps.length === 0 ? null : (
        <ul className="coverage__gaps">
          {coverage.gaps.map((gap) => (
            <li key={`${gap.from_ts}:${gap.to_ts}`}>
              <span className="mono">{gap.from_ts}</span> →{" "}
              <span className="mono">{gap.to_ts}</span> — {gap.reason}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
