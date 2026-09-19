/** A station state, rendered in the visual language.
 *
 * Three channels for one fact, and that is the point: the colour comes from the category,
 * the glyph is a distinct shape per category, and the PackML state name and the category
 * label are written out. ISA-101 forbids the colour being the only channel, and the
 * accessibility reason needs no citation — but the diagnostic reason is the stronger one.
 * A reader has to be able to tell "this station is the problem" from "this station is
 * waiting for the problem" on a greyscale print, on a projector with the colour washed
 * out, and read aloud.
 *
 * Every view M6 adds that shows a station state shows it through here. A view that paints
 * its own rectangle from `--category-*` would be re-deciding the language one screen at a
 * time, which is exactly what the colour channel losing its partner looks like.
 */
import { categoryFor, ENCODINGS, type Category } from "./stateCategory";

/** What an unrecognised state renders as. Not one of the five, and deliberately loud:
 * a state this application does not map is a fact about the data, and a grey fallback
 * would report it as a perfectly plausible "stopped". */
const UNKNOWN = {
  label: "unrecognised state",
  glyph: "⯀",
  meaning: "this application has no category for this state name",
} as const;

export function StateBadge({
  state,
  reason = null,
}: {
  /** The PackML state name as the analysis service reported it (§3.3). */
  state: string;
  /** `starved:B2_3` or `blocked:B3_4` when the state carries one. This is the whole
   * diagnostic value of a waiting station — *which* buffer, and which direction — so it
   * is shown rather than summarised (§3.3: the field is not optional). */
  reason?: string | null;
}) {
  const category: Category | null = categoryFor(state);
  const encoding = category === null ? UNKNOWN : ENCODINGS[category];

  return (
    <span
      className="state-badge"
      data-category={category ?? "unknown"}
      title={encoding.meaning}
    >
      <span className="state-badge__glyph" aria-hidden="true">
        {encoding.glyph}
      </span>
      <span className="state-badge__state">{state}</span>
      <span className="state-badge__category">{encoding.label}</span>
      {reason === null || reason === "" ? null : (
        <span className="state-badge__reason">{reason}</span>
      )}
    </span>
  );
}
