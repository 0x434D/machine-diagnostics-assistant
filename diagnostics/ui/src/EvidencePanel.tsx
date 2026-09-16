/** The row behind a citation.
 *
 * §7.2: a citation you cannot open is barely a citation. This is what opening one shows —
 * the record itself, not a summary of it.
 */
import { useEffect, useState } from "react";

import { describeFailure, fetchPart, imageUrl, type Part } from "./api";
import { useAuth } from "./AuthContext";

export function EvidencePanel({ serial }: { serial: string }) {
  const { token } = useAuth();
  const [part, setPart] = useState<Part | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    fetchPart(serial, token)
      .then((loaded) => current && setPart(loaded))
      .catch((reason: unknown) => current && setError(describeFailure(reason)));
    return () => {
      current = false;
    };
    // Re-fetches on a token change too: a panel opened before signing in shows its 401
    // rather than nothing (below), and pasting a token afterwards should not require
    // closing and reopening the chip to see the row it was always pointing at.
  }, [serial, token]);

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
  const inspection = part.inspection;
  return (
    <div data-testid="evidence-panel" className="evidence">
      <dl>
        <dt>Serial</dt>
        <dd>{part.assembly_serial}</dd>
        <dt>Created</dt>
        {/* Null is "this gateway never saw the assembly created", which is what every
            part already in the buffers when the gateway started looks like. Saying
            unknown is the point; omitting the part would be the failure. */}
        <dd>{part.created_at ?? "unknown"}</dd>
        <dt>Recorded</dt>
        <dd>{inspection?.source_ts ?? "not inspected"}</dd>
        <dt>Station</dt>
        <dd>{inspection?.station ?? "—"}</dd>
        <dt>Result</dt>
        <dd>{inspection?.result ?? "—"}</dd>
        <dt>Scores</dt>
        {/* Every class the classifier scored, not the one that won: §3.4's six are
            independent and do not sum to 1, and a part can be high on two at once. */}
        <dd>{scores(inspection) ?? "—"}</dd>
        <dt>Confidence</dt>
        <dd>
          {inspection?.confidence == null
            ? "—"
            : inspection.confidence.toFixed(3)}
        </dd>
        <dt>Model</dt>
        <dd>{inspection?.model_version ?? "—"}</dd>
      </dl>
      {image === null ? null : (
        <img src={image} alt={`Inspection image for ${serial}`} />
      )}
    </div>
  );
}

/** The verdict's per-class scores as `name score` pairs, or null when there are none. */
function scores(inspection: Part["inspection"]): string | null {
  const names = inspection?.defect_classes;
  const values = inspection?.confidences;
  if (names == null || values == null) return null;
  return names
    .map((name, index) => `${name} ${(values[index] ?? 0).toFixed(2)}`)
    .join(" · ");
}
