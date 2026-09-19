/** A citation, and the thing it opens.
 *
 * §7.3: adding a citation type means adding a renderer and nothing else. RENDERERS is that
 * seam, and at M6 every kind in the vocabulary is behind it — each resolving to the
 * endpoint §7.3 names for it, and each rendering what came back rather than a placeholder
 * repeating what the chip already said.
 */
import type { ReactElement } from "react";

import type { Citation } from "./generated/answer";
import { EvidencePanel } from "./EvidencePanel";
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
  // `id` is optional on the type because the composite kinds above have no use for it, not
  // because an id-shaped citation may lack one — a missing id here is the server sending
  // something the validator in `agent.answer.Citation` should have refused, so falling
  // back to the kind name reports that honestly instead of rendering nothing.
  return citation.id ?? citation.kind;
}

/** A citation with nothing in it to resolve.
 *
 * `agent.answer.Citation`'s validator makes this unconstructable, so reaching it means the
 * server sent a citation it should itself have rejected. That is worth saying out loud and
 * in one place: six renderers each writing their own version of this check is six chances
 * for one of them to render an empty panel instead, which reads as "nothing found".
 */
function Unaddressed({ citation }: { citation: Citation }) {
  return (
    <div
      data-testid="evidence-panel"
      data-outcome="unaddressed"
      className="evidence evidence--error"
    >
      Could not open this {citation.kind} citation: it carries none of the
      fields that would say what it refers to. The answer validator refuses such
      a citation, so this is the service having sent one it should have
      rejected.
    </div>
  );
}

/** The renderers whose kind is identified by one string, with the empty case handled once. */
function byId(
  render: (identifier: string) => ReactElement,
): (citation: Citation) => ReactElement {
  return (citation) =>
    citation.id == null || citation.id === "" ? (
      <Unaddressed citation={citation} />
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
  signal: (citation) =>
    citation.station == null || citation.signal == null ? (
      <Unaddressed citation={citation} />
    ) : (
      <SignalPanel station={citation.station} signal={citation.signal} />
    ),
  pattern: (citation) =>
    citation.dimension == null || citation.key == null ? (
      <Unaddressed citation={citation} />
    ) : (
      <PatternPanel dimension={citation.dimension} value={citation.key} />
    ),
  containment: (citation) => (
    <ContainmentPanel serials={citation.serials ?? []} />
  ),
};

export function CitationChip({ citation }: { citation: Citation }) {
  return (
    <Openable label={describe(citation)}>
      {RENDERERS[citation.kind](citation)}
    </Openable>
  );
}
