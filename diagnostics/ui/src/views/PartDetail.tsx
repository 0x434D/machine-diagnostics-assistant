/** §7.2's part detail: genealogy, station-by-station timeline, the process values recorded
 * for *that* part, its inspection result and image. Built by M6 Task 7.
 *
 * The route parameter is read and shown already, because a deep link from a citation is
 * the way a reader arrives here (§7.2: a citation you cannot open is barely a citation)
 * and a placeholder that ignored the serial would leave that wiring unproven.
 */
import { useParams } from "react-router";

import { Placeholder } from "./Placeholder";

export function PartDetail() {
  const { serial } = useParams<{ serial: string }>();

  return (
    <Placeholder title="Part detail" task="M6 Task 7">
      <p>
        Everything the line recorded about{" "}
        <span className="mono">{serial ?? "no serial in the URL"}</span> — its
        genealogy, its station-by-station timeline, its process values, its
        verdict and its image.
      </p>
    </Placeholder>
  );
}
