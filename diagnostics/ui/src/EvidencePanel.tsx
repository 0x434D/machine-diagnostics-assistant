/** The row behind a `part` citation.
 *
 * §7.2: a citation you cannot open is barely a citation. This is what opening one shows —
 * the record itself, not a summary of it.
 *
 * It resolves through `useResolution` and renders through `CitationPanel` like the other
 * eight kinds: the three outcomes a citation can have are written once, so this panel
 * cannot develop its own vocabulary for a failure the reader has already learned to read
 * somewhere else. The verdict and its image are `parts/InspectionRecord`, shared with §7.2's
 * part view — the same record read in two places, not written in two.
 *
 * It stops at the verdict on purpose. Genealogy, the station history and the process values
 * are in the part view this panel's serial links to; a citation opens onto the row it cites,
 * and the whole of §14's trace inside an inline panel would bury it.
 */
import { fetchPart } from "./api";
import { CitationPanel } from "./citations/CitationPanel";
import { useResolution } from "./citations/useResolution";
import { InspectionRecord } from "./parts/InspectionRecord";

export function EvidencePanel({ serial }: { serial: string }) {
  const resolution = useResolution(`part:${serial}`, (token) =>
    fetchPart(serial, token),
  );

  return (
    <CitationPanel what={serial} resolution={resolution}>
      {(part) => (
        <>
          <dl>
            <dt>Serial</dt>
            <dd>{part.assembly_serial}</dd>
            <dt>Created</dt>
            {/* Null is "this gateway never saw the assembly created", which is what every
                part already in the buffers when the gateway started looks like. Saying
                unknown is the point; omitting the part would be the failure. */}
            <dd>{part.created_at ?? "unknown"}</dd>
          </dl>
          <InspectionRecord serial={serial} part={part} />
        </>
      )}
    </CitationPanel>
  );
}
