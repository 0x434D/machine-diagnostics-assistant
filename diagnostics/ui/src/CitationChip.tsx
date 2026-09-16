/** A citation, and the thing it opens.
 *
 * §7.3: adding a citation type means adding a renderer and nothing else. RENDERERS is
 * that seam — M1 registers `part`, and the kinds that arrive with later analysis
 * endpoints register themselves here without this component changing.
 */
import { useState, type ReactElement } from "react";

import type { Citation } from "./generated/answer";
import { EvidencePanel } from "./EvidencePanel";

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

/** M4's renderer for the eight kinds with no analysis endpoint yet: the chip already shows
 * everything the answer carries about the citation, so "opening" it shows that same
 * information rather than nothing. M6 replaces this per kind as each gets a real endpoint. */
function CitationDetail({ citation }: { citation: Citation }): ReactElement {
  return (
    <span className="citation__detail">
      {citation.kind}: {describe(citation)}
    </span>
  );
}

// Keyed by the generated Citation["kind"] union rather than by string, so §7.3's claim is
// enforced rather than intended: a kind added to contracts/answer.schema.json fails to compile
// here until it has a renderer, which is the difference between a seam and a hope.
export const RENDERERS: Record<
  Citation["kind"],
  (citation: Citation) => ReactElement
> = {
  // The one kind with a real endpoint before M6. A `part` citation is expected to carry
  // an id; the fallback below only guards the type, not a real case, and matches every
  // other kind's honest state rather than throwing on data the validator should have caught.
  part: (citation) =>
    citation.id == null ? (
      <CitationDetail citation={citation} />
    ) : (
      <EvidencePanel serial={citation.id} />
    ),
  stop: (citation) => <CitationDetail citation={citation} />,
  alarm: (citation) => <CitationDetail citation={citation} />,
  signal: (citation) => <CitationDetail citation={citation} />,
  pattern: (citation) => <CitationDetail citation={citation} />,
  sop: (citation) => <CitationDetail citation={citation} />,
  serial: (citation) => <CitationDetail citation={citation} />,
  lot: (citation) => <CitationDetail citation={citation} />,
  containment: (citation) => <CitationDetail citation={citation} />,
};

export function CitationChip({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  const render = RENDERERS[citation.kind];

  return (
    <span className="citation">
      <button
        type="button"
        className="citation__chip"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {describe(citation)}
      </button>
      {open ? render(citation) : null}
    </span>
  );
}
