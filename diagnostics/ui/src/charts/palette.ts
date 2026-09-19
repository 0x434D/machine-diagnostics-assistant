/** The chart's colours, read out of the design tokens rather than written a second time.
 *
 * `design/tokens.css` says *"nothing else in this application writes a colour"*, and a chart
 * is the easiest place in a frontend to break that: a charting library ships a palette, the
 * palette looks fine, and the screen quietly grows a sixth grey and a second language for
 * station state. So every colour a chart uses is a custom property resolved off the document
 * at render time — which also makes light and dark free, because the token file redefines
 * the same names under `prefers-color-scheme` and the computed value follows the reader.
 *
 * **Colour is never the only channel** (ISA-101, and §15's reason: the visual language has
 * to teach the diagnostic model). Where a chart encodes station state it encodes §15's five
 * categories, and `LEGEND` below spells each one out with the glyph `StateBadge` uses — so
 * the distinction survives a greyscale print, a colour-blind reader, and a token file that
 * failed to load.
 */
import "../design/tokens.css";

import {
  CATEGORIES,
  ENCODINGS,
  categoryFor,
  type Category,
} from "../design/stateCategory";

/** A custom property as the document currently resolves it.
 *
 * Empty when the stylesheet has not loaded, and the callers pass that straight to Vega,
 * which then draws in its own default. That degradation is deliberate and is why the second
 * channel is not optional: a chart whose colours did not arrive is still readable, and a
 * chart whose colours are the only channel is not readable to begin with.
 */
function token(name: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

/** The five category colours, in `CATEGORIES` order, for a Vega scale range. */
export function categoryRange(): string[] {
  return CATEGORIES.map((category) => token(`--category-${category}`));
}

/** The written label and the glyph for each category, as a Vega scale domain would order
 * them. The glyph rides in the label so that a legend entry is a shape and a word before it
 * is a colour. */
export const LEGEND: Record<Category, string> = Object.fromEntries(
  CATEGORIES.map((category) => [
    category,
    `${ENCODINGS[category].glyph} ${ENCODINGS[category].label}`,
  ]),
) as Record<Category, string>;

/** What a PackML state name this application does not map renders as.
 *
 * Deliberately loud, exactly as `StateBadge` is: a grey fallback would draw an unmapped
 * state as a perfectly plausible "stopped" and nothing anywhere would fail.
 */
export const UNMAPPED = "⯀ unrecognised state";

/** The category of a row's state, as the reader sees it written.
 *
 * Computed here and not by the model: the mapping from a PackML state to a category is
 * §3.3's and §15's, and a model that could assign it would be assigning the cause/consequence
 * distinction the whole chart exists to show.
 */
export function categoryLabel(state: unknown): string {
  const category = categoryFor(String(state));
  return category === null ? UNMAPPED : LEGEND[category];
}

/** The glyph alone, for the text mark that rides on a coloured bar. */
export function categoryGlyph(state: unknown): string {
  const category = categoryFor(String(state));
  return category === null ? "⯀" : ENCODINGS[category].glyph;
}

/** The scale a state-coloured chart uses: the five labels, in order, and their colours,
 * with the sentinel for anything this application does not recognise. */
export function stateScale(): { domain: string[]; range: string[] } {
  return {
    domain: [...CATEGORIES.map((category) => LEGEND[category]), UNMAPPED],
    range: [...categoryRange(), token("--category-unknown")],
  };
}

/** How many series colours `tokens.css` declares. A seventh value recycles the first, which
 * is why every chart that uses this range also labels its marks. */
const SERIES = 6;

/** The palette for a dimension that is **not** station state — a defect class, a carrier, a
 * lane. `tokens.css` says at length why these are not the five category hues. */
export function seriesRange(): string[] {
  return Array.from({ length: SERIES }, (_, index) =>
    token(`--chart-series-${String(index + 1)}`),
  );
}

/** The non-categorical colours: one accent for a series, the type and grid colours that
 * make the chart belong to the page it is on. */
export function chrome(): {
  accent: string;
  text: string;
  muted: string;
  grid: string;
  surface: string;
  danger: string;
  font: string;
} {
  return {
    accent: token("--accent"),
    text: token("--text"),
    muted: token("--text-muted"),
    grid: token("--border"),
    surface: token("--surface-raised"),
    danger: token("--danger"),
    // Vega draws SVG text with its own font settings and inherits nothing from the
    // stylesheet, so the chart would otherwise be the one thing on the page in Helvetica.
    font: token("--font-sans"),
  };
}
