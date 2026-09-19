/** Serials, as things to open rather than things to read.
 *
 * Used where a record names parts it is not itself about — the assembly a cited component
 * went into, the parts a lot reached, the scope a containment claim was made over. Each of
 * those is a `part` citation in everything but name, so each opens the way one does, and a
 * reader never has to copy a serial out into a search box to see what happened to it.
 */
import { EvidencePanel } from "../EvidencePanel";
import { Openable } from "./Openable";

export function PartLinks({ serials }: { serials: readonly string[] }) {
  return (
    <span className="serials">
      {serials.map((serial) => (
        <Openable key={serial} label={serial}>
          <EvidencePanel serial={serial} />
        </Openable>
      ))}
    </span>
  );
}
