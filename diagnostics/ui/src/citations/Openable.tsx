/** A chip that opens onto what it names.
 *
 * Extracted from `CitationChip` because the same gesture appears inside the panels a
 * citation opens — a cited component names the assembly it went into, a cited lot names
 * the parts it reached, a containment scope *is* a list of parts — and each of those is
 * something to open rather than a serial to copy out by hand. One component so the chip in
 * an answer and the chip inside a panel cannot drift into two different affordances.
 *
 * `children` is an element and not a function, and React's laziness is what makes that
 * safe: the element is described on every render and mounted only while the chip is open,
 * so nothing behind a closed chip fetches anything.
 */
import { useState, type ReactNode } from "react";

export function Openable({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <span className="citation">
      <button
        type="button"
        className="citation__chip"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
        }}
      >
        {label}
      </button>
      {open ? children : null}
    </span>
  );
}
