/** The row behind a `part` citation.
 *
 * §7.2: a citation you cannot open is barely a citation. This is what opening one shows —
 * the record itself, not a summary of it.
 *
 * It resolves through `useResolution` and renders through `CitationPanel` like the other
 * eight kinds: the three outcomes a citation can have are written once, so this panel
 * cannot develop its own vocabulary for a failure the reader has already learned to read
 * somewhere else. What is only this kind's is the image, which is a second fetch and lives
 * below, in a component that exists only once the part is known.
 */
import { useEffect, useState } from "react";

import {
  describeFailure,
  fetchImage,
  fetchPart,
  imagePath,
  type Part,
} from "./api";
import { useAuth } from "./AuthContext";
import { CitationPanel } from "./citations/CitationPanel";
import { useResolution } from "./citations/useResolution";

export function EvidencePanel({ serial }: { serial: string }) {
  const resolution = useResolution(`part:${serial}`, (token) =>
    fetchPart(serial, token),
  );

  return (
    <CitationPanel what={serial} resolution={resolution}>
      {(part) => <PartRecord serial={serial} part={part} />}
    </CitationPanel>
  );
}

function PartRecord({ serial, part }: { serial: string; part: Part }) {
  const { token } = useAuth();
  const [image, setImage] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);

  useEffect(() => {
    const path = imagePath(part);
    if (path === null) return;

    // `revoked` closes the window in which the teardown runs before the fetch resolves: an
    // object URL created after that point would have no owner left to release it. A blob URL
    // that outlives its <img> pins the image bytes for the life of the document, and a
    // reader clicking through citations for an afternoon creates one per click.
    let revoked = false;
    let created: string | null = null;

    fetchImage(path, token)
      .then((bytes) => {
        if (revoked) return;
        created = URL.createObjectURL(bytes);
        setImage(created);
      })
      .catch((reason: unknown) => {
        if (!revoked) setImageError(describeFailure(reason));
      });

    return () => {
      revoked = true;
      if (created !== null) URL.revokeObjectURL(created);
      setImage(null);
      setImageError(null);
    };
  }, [part, token]);

  const inspection = part.inspection;
  return (
    <>
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
      {/* Three states, not two. No image at all is §3.4's good part and shows nothing;
          bytes that would not load is a failure and says so, because the image is the
          evidence the citation was clicked for and a blank space where it should be reads
          as "this part has no image" — which is a different and wrong fact. */}
      {imageError !== null ? (
        <p className="evidence__image-error">
          Could not open the inspection image: {imageError}
        </p>
      ) : image === null ? null : (
        <img src={image} alt={`Inspection image for ${serial}`} />
      )}
    </>
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
