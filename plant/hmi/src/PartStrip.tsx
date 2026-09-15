import { plantUrl } from "./plantApi";
import type { PartView } from "./snapshot";

/** §3.7's strip of the last parts, newest first, with a thumbnail on every reject.
 *
 * Undesigned on purpose, like the rest of this screen (§15): the visual language is
 * deferred, and what is here is the serial, the verdict, the reason a reject was
 * rejected, and the picture the verdict was made from.
 *
 * The serial is on the strip rather than a count of good and bad, because the serial is
 * the thing M2b gave every part and the thing the diagnostics stack can be asked about.
 * Reading one off this screen and pasting it into the other one is the demo.
 */
export function PartStrip({ parts }: { parts: PartView[] }) {
  if (parts.length === 0) {
    // Not an empty list rendered as nothing: before the first part is inspected there
    // genuinely are none, and a blank area reads as a strip that failed to load.
    return <p className="parts parts--empty">no parts inspected yet</p>;
  }

  return (
    <ol className="parts">
      {parts.map((part) => (
        <li
          key={part.serial}
          className={`part part--${part.disposition}`}
          data-testid="part"
        >
          <span className="part__serial">{part.serial}</span>
          {/* The verdict as text beside the colour, for the reason every station tile
              carries its state name: colour is never the only channel (ISA-101). */}
          <span className="part__disposition">{part.disposition}</span>
          {part.reason !== "" && (
            <span className="part__reason">{part.reason}</span>
          )}
          {part.image_url !== null && (
            <img
              className="part__image"
              src={plantUrl(part.image_url)}
              alt={`Inspection image for ${part.serial}`}
            />
          )}
        </li>
      ))}
    </ol>
  );
}
