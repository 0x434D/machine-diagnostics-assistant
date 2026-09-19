/** §15's banked idea, which named M6 as its place: **colour by category, not by state.**
 *
 * `Suspended` is a consequence by definition — the station is starved or blocked because
 * someone else stopped — while `Held` and `Aborted` are cause candidates (§3.3). §5.4's
 * whole derivation is that distinction, and a screen that paints fifteen PackML states in
 * fifteen colours hides it. Five categories paint it: one colour on the station that *is*
 * the problem, a different one on the stations merely waiting for it.
 *
 * **Colour is never the only channel** (ISA-101). Every category here carries a glyph and
 * a written label alongside the colour, so the distinction survives a greyscale print, a
 * colour-blind reader and a screen reader. `StateBadge` renders all three; the test in
 * `src/__tests__/stateCategory.test.tsx` is what stops a later view rendering only the
 * colour.
 *
 * The five names and the state-to-category mapping are a deliberate copy of
 * `plant/simulator/src/simulator/hmi.py` — §7 shares design tokens between the two
 * frontends and nothing else, and a viewer who reads both screens should have one colour
 * language to learn rather than two. The copy is one direction of a boundary this project
 * exists to keep; an import across it would be the coupling.
 */

export const CATEGORIES = [
  "producing",
  "waiting-on-others",
  "held-by-own-fault",
  "stopped",
  "transitioning",
] as const;

export type Category = (typeof CATEGORIES)[number];

/** How a category reads when the colour is gone.
 *
 * `glyph` is the shape channel and `label` the written one. Both are required, and both
 * are unique across the five — a shared glyph would make two categories identical in
 * greyscale, which is the failure this file exists to prevent.
 */
export interface CategoryEncoding {
  label: string;
  glyph: string;
  /** What the category means for diagnosis, for the legend and for a title attribute. */
  meaning: string;
}

export const ENCODINGS: Record<Category, CategoryEncoding> = {
  producing: {
    label: "producing",
    glyph: "●",
    meaning: "running and making parts",
  },
  "waiting-on-others": {
    label: "waiting on others",
    glyph: "◐",
    meaning:
      "a consequence — starved or blocked by another station, and it clears itself",
  },
  "held-by-own-fault": {
    label: "held by own fault",
    glyph: "▲",
    meaning: "a cause candidate — this is the station to go and look at",
  },
  stopped: {
    label: "stopped",
    glyph: "■",
    meaning: "not running, and nothing wrong",
  },
  transitioning: {
    label: "transitioning",
    glyph: "◆",
    meaning: "on its way back up",
  },
};

/** §3.3's fifteen states, each in exactly one category.
 *
 * Exhaustive over the PackML subset the line runs. The value arrives from the analysis
 * service as a free string (`StateEpisode.state`, `StationStatus.state`), so this cannot
 * be a compile-time total function — `categoryFor` returns null for anything else and the
 * badge says so in a colour no plant screen would legitimately show, rather than picking
 * a plausible one.
 */
const CATEGORY_BY_STATE: Record<string, Category> = {
  Execute: "producing",

  // The consequence side. `Unsuspending` is here rather than under transitioning because
  // the station is still waiting on the buffer it was suspended for until it settles, and
  // a station that flickered green on its way out of a starvation would read as a line
  // that had recovered when it had not.
  Suspending: "waiting-on-others",
  Suspended: "waiting-on-others",
  Unsuspending: "waiting-on-others",

  // The cause candidates: none of these clears itself, and every one of them is a station
  // to go and look at. `Unholding` included — it is a station coming out of its own fault,
  // which is still that station's episode.
  Holding: "held-by-own-fault",
  Held: "held-by-own-fault",
  Unholding: "held-by-own-fault",
  Aborting: "held-by-own-fault",
  Aborted: "held-by-own-fault",

  // Not running, and nothing wrong. `Stopping` settles into `Stopped`, and `Idle` is a
  // station that has been reset and not yet started.
  Stopping: "stopped",
  Stopped: "stopped",
  Idle: "stopped",

  // Coming up: the three acting states on the path from `Aborted` back to `Execute`.
  Clearing: "transitioning",
  Resetting: "transitioning",
  Starting: "transitioning",
};

/** Which category a PackML state name renders as, or null for a name this application
 * does not know. Null is a real answer and not a failure: the state is a string on the
 * wire, and reporting "I do not recognise this" is the honest reading of one that has
 * been renamed or added upstream. */
export function categoryFor(state: string): Category | null {
  return CATEGORY_BY_STATE[state] ?? null;
}

/** Every state name this application maps, for the exhaustiveness test and for a legend
 * that wants to show which states sit under a category. */
export function statesIn(category: Category): string[] {
  return Object.keys(CATEGORY_BY_STATE).filter(
    (state) => CATEGORY_BY_STATE[state] === category,
  );
}
