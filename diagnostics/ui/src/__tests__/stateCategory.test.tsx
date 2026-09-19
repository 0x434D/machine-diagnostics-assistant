/** §15 and ISA-101: colour is never the only channel.
 *
 * These are the checks Task 9 repeats against a running browser. What they assert is the
 * property, not the implementation: that each of the five categories arrives at the reader
 * through something other than its colour, and that the five stay distinguishable when the
 * colour is taken away entirely.
 */
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { StateBadge } from "../design/StateBadge";
import { StateLegend } from "../design/StateLegend";
import {
  CATEGORIES,
  categoryFor,
  ENCODINGS,
  statesIn,
  type Category,
} from "../design/stateCategory";

/** §3.3's fifteen, which is the whole PackML subset the line runs. Written out rather than
 * imported, because the plant's enum is one stack over and §7 says these two frontends
 * share design tokens and nothing else — so what this guards is that the copy is complete,
 * which a shared import would make unguardable. */
const PACKML_STATES = [
  "Aborted",
  "Stopped",
  "Idle",
  "Execute",
  "Held",
  "Suspended",
  "Aborting",
  "Clearing",
  "Stopping",
  "Resetting",
  "Starting",
  "Holding",
  "Unholding",
  "Suspending",
  "Unsuspending",
];

test("every state the line can be in has a category", () => {
  // A state with no category renders in the sentinel colour and reads as broken. That is
  // the right behaviour for a state name nobody has seen before, and the wrong one for
  // `Unsuspending`, which the line enters several times an hour.
  for (const state of PACKML_STATES) {
    expect(categoryFor(state), state).not.toBeNull();
  }
});

test("a state name this application does not know is not given a plausible category", () => {
  // The alternative — falling back to `stopped` — reports an unmapped state as a perfectly
  // ordinary one, and nothing anywhere fails.
  expect(categoryFor("Completing")).toBeNull();
  expect(categoryFor("")).toBeNull();
});

test("the cause/consequence distinction is the one the categories draw", () => {
  // §3.3 is what makes §5.4's derivation possible: `Suspended` is a consequence by
  // definition and clears itself, while `Held` and `Aborted` need someone to walk over.
  // If these two ever land in the same category the screen stops teaching the difference,
  // which is the entire reason §15 banked this idea.
  expect(categoryFor("Suspended")).toBe("waiting-on-others");
  expect(categoryFor("Held")).toBe("held-by-own-fault");
  expect(categoryFor("Aborted")).toBe("held-by-own-fault");
  expect(categoryFor("Suspended")).not.toBe(categoryFor("Held"));
});

test("each category carries a second channel that is not its colour", () => {
  for (const category of CATEGORIES) {
    const state = statesIn(category)[0];
    if (state === undefined) throw new Error(`${category} has no states`);

    const { unmount } = render(<StateBadge state={state} />);
    // The state name, in words.
    expect(screen.getByText(state)).toBeInTheDocument();
    // The category, in words.
    expect(screen.getByText(ENCODINGS[category].label)).toBeInTheDocument();
    // And a shape.
    expect(screen.getByText(ENCODINGS[category].glyph)).toBeInTheDocument();
    unmount();
  }
});

test("the five stay distinguishable with the colour removed", () => {
  // The greyscale test, done the way a greyscale rendering actually works: whatever is
  // left when the colour is gone is the text and the shapes, so those have to differ.
  // A shared glyph or a shared label would make two categories identical on a printout
  // and identical to a screen reader.
  const glyphs = new Set(CATEGORIES.map((c) => ENCODINGS[c].glyph));
  const labels = new Set(CATEGORIES.map((c) => ENCODINGS[c].label));

  expect(glyphs.size).toBe(CATEGORIES.length);
  expect(labels.size).toBe(CATEGORIES.length);
  for (const glyph of glyphs) expect(glyph).not.toBe("");
});

test("two badges of different categories do not read the same", () => {
  // The property the two sets above stand for, asserted on what is actually rendered:
  // strip the colour and the badges still say different things.
  const rendered = CATEGORIES.map((category: Category) => {
    const state = statesIn(category)[0] ?? category;
    const { container, unmount } = render(<StateBadge state={state} />);
    const text = container.textContent ?? "";
    unmount();
    return text;
  });

  expect(new Set(rendered).size).toBe(CATEGORIES.length);
});

test("the badge shows why a station is waiting when the state says why", () => {
  // §3.3: every `Suspended` transition records which buffer and which direction, and that
  // field is the whole diagnostic value of a waiting station. Dropping it would leave the
  // screen saying "waiting" without saying for what.
  render(<StateBadge state="Suspended" reason="starved:B2_3" />);
  expect(screen.getByText("starved:B2_3")).toBeInTheDocument();
});

test("the reader is told what the colours mean", () => {
  // §15's claim is that the visual language teaches the diagnostic model. It only does
  // that for a reader who has been told which colour is the cause and which the
  // consequence, so the key is on screen rather than in a comment.
  render(<StateLegend />);
  for (const category of CATEGORIES) {
    // `getAllByText`: the lede names two of the categories in prose, which is the sentence
    // doing its job rather than a duplicate to be tidied away.
    expect(
      screen.getAllByText(ENCODINGS[category].label).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(ENCODINGS[category].meaning)).toBeInTheDocument();
  }
});
