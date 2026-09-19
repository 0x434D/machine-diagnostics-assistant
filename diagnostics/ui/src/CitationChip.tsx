/** A citation, and the thing it opens.
 *
 * §7.3: adding a citation type means adding a renderer and nothing else. RENDERERS is that
 * seam, and at M6 every kind in the vocabulary is behind it — each resolving to the
 * endpoint §7.3 names for it, or, for `chart`, to the tool call §7.4 makes its referent —
 * and each rendering what came back rather than a placeholder repeating what the chip
 * already said.
 */
import type { ReactElement } from "react";

import type { Citation } from "./generated/answer";
import { EvidencePanel } from "./EvidencePanel";
import { ChartPanel, chartLabel } from "./charts/ChartPanel";
import { Openable } from "./citations/Openable";
import {
  AlarmPanel,
  ComponentPanel,
  ContainmentPanel,
  LotPanel,
  PatternPanel,
  SignalPanel,
  SopPanel,
  StopPanel,
} from "./citations/panels";

/** What a citation refers to, in the id-shaped kinds and the three composite ones. §7.3
 * gives `signal`, `pattern` and `containment` their own fields rather than an id — see the
 * schema comment on `Citation` — so this reads whichever fields the kind actually carries
 * instead of assuming `id`, which is the thing that stopped the map from type-checking. */
function describe(citation: Citation): string {
  if (citation.kind === "signal") {
    return `${citation.station ?? "?"}/${citation.signal ?? "?"}`;
  }
  if (citation.kind === "pattern") {
    return `${citation.dimension ?? "?"}=${citation.key ?? "?"}`;
  }
  if (citation.kind === "containment") {
    return `${String(citation.serials?.length ?? 0)} part(s)`;
  }
  if (citation.kind === "chart") {
    return chartLabel(citation);
  }
  // `id` is optional on the type because the composite kinds above have no use for it, not
  // because an id-shaped citation may lack one — a missing id here is the server sending
  // something the validator in `agent.answer.Citation` should have refused, so falling
  // back to the kind name reports that honestly instead of rendering nothing.
  return citation.id ?? citation.kind;
}

/** A citation missing something it needs before anything can be opened.
 *
 * `agent.answer.Citation`'s validator makes a citation without its payload unconstructable,
 * and `agent.answer.Answer` refuses a `pattern` or `signal` citation that carries no window
 * (§7.3) — so reaching this means the server sent a citation it should itself have rejected.
 * That is worth saying out loud and in one place: six renderers each writing their own
 * version of this check is six chances for one of them to render an empty panel instead,
 * which reads as "nothing found".
 */
function Unaddressed({
  citation,
  missing,
}: {
  citation: Citation;
  missing: readonly string[];
}) {
  return (
    <div
      data-testid="evidence-panel"
      data-outcome="unaddressed"
      className="evidence evidence--error"
    >
      Could not open this {citation.kind} citation: it carries no{" "}
      {missing.join(", no ")}, so there is nothing to say what it refers to. The
      answer object refuses such a citation, so this is the service having sent
      one it should have rejected.
    </div>
  );
}

/** Which of the fields a kind needs are not on the citation, for the sentence above.
 *
 * Named rather than counted: "it carries no window" and "it carries no dimension, no key"
 * send a reader to different places, and a renderer that said only *something* was missing
 * would make them open the network tab to find out which.
 */
function absent(citation: Citation, fields: readonly string[]): string[] {
  return fields.filter((field) => {
    const value = citation[field];
    return value === null || value === undefined || value === "";
  });
}

/** The renderers whose kind is identified by one string, with the empty case handled once. */
function byId(
  render: (identifier: string) => ReactElement,
): (citation: Citation) => ReactElement {
  return (citation) =>
    citation.id == null || citation.id === "" ? (
      <Unaddressed citation={citation} missing={["id"]} />
    ) : (
      render(citation.id)
    );
}

// Keyed by the generated Citation["kind"] union rather than by string, so §7.3's claim is
// enforced rather than intended: a kind added to contracts/answer.schema.json fails to compile
// here until it has a renderer, which is the difference between a seam and a hope.
export const RENDERERS: Record<
  Citation["kind"],
  (citation: Citation) => ReactElement
> = {
  part: byId((serial) => <EvidencePanel serial={serial} />),
  stop: byId((identifier) => <StopPanel identifier={identifier} />),
  alarm: byId((identifier) => <AlarmPanel identifier={identifier} />),
  sop: byId((documentId) => <SopPanel documentId={documentId} />),
  // A *component* serial and not an assembly one — see `ComponentPanel`, and RULING M4-R5.
  serial: byId((serial) => <ComponentPanel serial={serial} />),
  lot: byId((lotCode) => <LotPanel lotCode={lotCode} />),
  // The two kinds §7.3 gives a window to. Their endpoints take `from` and `to`, so a
  // citation without one names no interval to open over — and opening a recent one instead
  // would put real rows from the database under a sentence that was never about them.
  signal: (citation) =>
    citation.station != null &&
    citation.signal != null &&
    citation.window != null ? (
      <SignalPanel
        station={citation.station}
        signal={citation.signal}
        window={citation.window}
      />
    ) : (
      <Unaddressed
        citation={citation}
        missing={absent(citation, ["station", "signal", "window"])}
      />
    ),
  pattern: (citation) =>
    citation.dimension != null &&
    citation.key != null &&
    citation.window != null ? (
      <PatternPanel
        dimension={citation.dimension}
        value={citation.key}
        window={citation.window}
      />
    ) : (
      <Unaddressed
        citation={citation}
        missing={absent(citation, ["dimension", "key", "window"])}
      />
    ),
  containment: (citation) => (
    <ContainmentPanel serials={citation.serials ?? []} />
  ),
  // The one kind whose referent is not a row in the database but a tool call this run made
  // (§7.4). It resolves through §7.2's trace for the exchange the answer came from, which
  // is why it takes the whole citation and reads the page's exchange rather than an id.
  chart: (citation) =>
    citation.chart_type != null && citation.source != null ? (
      <ChartPanel citation={citation} />
    ) : (
      <Unaddressed
        citation={citation}
        missing={absent(citation, ["chart_type", "source"])}
      />
    ),
};

export function CitationChip({ citation }: { citation: Citation }) {
  return (
    <Openable label={describe(citation)}>
      {RENDERERS[citation.kind](citation)}
    </Openable>
  );
}
