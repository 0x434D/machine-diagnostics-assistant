/** §3.4's verdict for one part, and the image the verdict was reached on.
 *
 * Two readers show it: the panel a `part` citation opens, and §7.2's part view. Written once
 * rather than twice because of the second half — **a browser cannot put an `Authorization`
 * header on an `<img>`**, M5 had to fix exactly that once already, and a second copy of the
 * fetch is a second chance to hand the endpoint back to the browser.
 *
 * Absence here is never a blank. A part with no verdict has not reached the camera, and a
 * verdict with no image is §3.4's good part; both are ordinary, and each says which it is.
 */
import { useEffect, useState } from "react";

import { describeFailure, fetchImage, imagePath, type Part } from "../api";
import { useAuth } from "../AuthContext";

export function InspectionRecord({
  serial,
  part,
}: {
  serial: string;
  part: Part;
}) {
  const inspection = part.inspection;

  return (
    <>
      <dl>
        <dt>Recorded</dt>
        <dd>{inspection?.source_ts ?? "not inspected"}</dd>
        <dt>Station</dt>
        <dd>{inspection?.station ?? "—"}</dd>
        <dt>Result</dt>
        <dd>{inspection?.result ?? "—"}</dd>
        <dt>Scores</dt>
        {/* Every class the classifier scored, not the one that won: §3.4's six are
            independent and do not sum to 1, and a part can be high on two at once. */}
        <dd>{describeScores(inspection) ?? "—"}</dd>
        <dt>Confidence</dt>
        <dd>
          {inspection?.confidence == null
            ? "—"
            : inspection.confidence.toFixed(3)}
        </dd>
        <dt>Model</dt>
        <dd>{inspection?.model_version ?? "—"}</dd>
      </dl>
      <InspectionImage serial={serial} part={part} />
    </>
  );
}

/** The verdict's per-class scores as `name score` pairs, or null when there are none. */
export function describeScores(inspection: Part["inspection"]): string | null {
  const names = inspection?.defect_classes;
  const values = inspection?.confidences;
  if (names == null || values == null) return null;
  return names
    .map((name, index) => `${name} ${(values[index] ?? 0).toFixed(2)}`)
    .join(" · ");
}

/** The reject image, fetched under the reader's own identity and released when it goes.
 *
 * Three states, not two. No image at all is §3.4's good part and shows nothing; bytes that
 * would not load is a failure and says so, because the image is the evidence the reader came
 * for and a blank space where it should be reads as "this part has no image" — which is a
 * different and wrong fact.
 */
function InspectionImage({ serial, part }: { serial: string; part: Part }) {
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

  if (imageError !== null) {
    return (
      <p className="evidence__image-error">
        Could not open the inspection image: {imageError}
      </p>
    );
  }
  if (image === null) return null;
  return <img src={image} alt={`Inspection image for ${serial}`} />;
}
