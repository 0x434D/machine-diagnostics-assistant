/** The row behind a citation.
 *
 * §7.2: a citation you cannot open is barely a citation. This is what opening one shows —
 * the record itself, not a summary of it.
 */
import { useEffect, useState } from "react";

import { fetchPart, imageUrl, type Part } from "./api";

export function EvidencePanel({ serial }: { serial: string }) {
  const [part, setPart] = useState<Part | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    fetchPart(serial)
      .then((loaded) => current && setPart(loaded))
      .catch((reason: unknown) => current && setError(String(reason)));
    return () => {
      current = false;
    };
  }, [serial]);

  if (error !== null) {
    return (
      <div data-testid="evidence-panel" className="evidence evidence--error">
        {/* A citation that does not resolve is reported, never blanked: §6.5 depends on
            the difference between "no such id" and "nothing to show". */}
        Could not open {serial}: {error}
      </div>
    );
  }

  if (part === null) {
    return (
      <div data-testid="evidence-panel" className="evidence">
        Opening {serial}…
      </div>
    );
  }

  const image = imageUrl(part);
  return (
    <div data-testid="evidence-panel" className="evidence">
      <dl>
        <dt>Serial</dt>
        <dd>{part.assembly_serial}</dd>
        <dt>Recorded</dt>
        <dd>{part.source_ts}</dd>
        <dt>Station</dt>
        <dd>{part.station}</dd>
        <dt>Result</dt>
        <dd>{part.result}</dd>
        <dt>Defect</dt>
        <dd>{part.defect_class ?? "—"}</dd>
        <dt>Confidence</dt>
        <dd>{part.confidence === null ? "—" : part.confidence.toFixed(3)}</dd>
        <dt>Model</dt>
        <dd>{part.model_version ?? "—"}</dd>
      </dl>
      {image === null ? null : (
        <img src={image} alt={`Inspection image for ${serial}`} />
      )}
    </div>
  );
}
