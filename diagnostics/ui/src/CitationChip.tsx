/** A citation, and the thing it opens.
 *
 * §7.3: adding a citation type means adding a renderer and nothing else. RENDERERS is
 * that seam — M1 registers `part`, and the kinds that arrive with later analysis
 * endpoints register themselves here without this component changing.
 */
import { useState, type ReactElement } from "react";

import type { Citation } from "./generated/answer";
import { EvidencePanel } from "./EvidencePanel";

export const RENDERERS: Record<Citation["kind"], (id: string) => ReactElement> =
  {
    part: (id) => <EvidencePanel serial={id} />,
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
        {citation.id}
      </button>
      {open ? render(citation.id) : null}
    </span>
  );
}
