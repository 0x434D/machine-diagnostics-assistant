/** The application at phone width.
 *
 * §7.2: every user is a browser client, whether that browser is on a tablet at the line or
 * on a desk. A phone-width viewport is therefore a supported one, and the failure it
 * suffers first is horizontal scroll — the page slides sideways under a thumb and half of
 * every line is off-screen.
 *
 * **jsdom performs no layout**, so this cannot measure a rendered page; `scrollWidth` is
 * zero for everything. What it measures instead is the cause: a page scrolls sideways
 * because something in it insists on being wider than the viewport, and every such
 * insistence is a declaration in the stylesheet. The narrowest supported width is not a
 * number kept here — it is `--viewport-min`, read out of the token file, so the test and
 * the design agree by construction. Task 9 makes the same check against a real browser.
 */
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import indexHtml from "../../index.html?raw";
import styles from "../styles.css?raw";
import tokens from "../design/tokens.css?raw";

const STYLESHEET = `${tokens}\n${styles}`;

/** The root font size every rem below is resolved against. The application never changes
 * it, so this is the browser default rather than a setting. */
const ROOT_FONT_PX = 16;

/** Declarations that can force an element to be wider than its container. `max-width` is
 * absent on purpose: it caps a width, it never demands one. So is `%`, which is relative
 * to a container that is itself within budget. */
const HARD_CONSTRAINTS = ["min-width", "width", "flex-basis"];

function toPixels(value: string, unit: string): number | null {
  const size = Number(value);
  if (Number.isNaN(size)) return null;
  if (unit === "px") return size;
  if (unit === "rem" || unit === "em") return size * ROOT_FONT_PX;
  return null;
}

/** Every horizontal constraint in `css` that demands more than `limit` pixels.
 *
 * Declarations inside an `@media (min-width: …)` wider than the limit are skipped: they do
 * not apply at the width being tested, which is the whole point of writing them that way.
 */
function tooWide(css: string, limit: number): string[] {
  const offenders: string[] = [];
  let depth = 0;
  let condition: string | null = null;
  let conditionDepth = 0;

  for (const raw of declarations(css)) {
    const line = raw.trim();
    const media = /^@media\s*([^{]*)\{/.exec(line);
    if (media !== null && condition === null) {
      condition = media[1] ?? "";
      conditionDepth = depth;
    }

    // A custom property is a value in the palette, not a rule applied to an element.
    // `--measure-wide: 80rem` is the width a desk browser may grow to, and reading it as
    // a demand would make the design impossible to express.
    const isToken = line.startsWith("--");
    const appliesHere =
      condition === null ||
      !exceeds(/min-width:\s*([\d.]+)(px|rem|em)/, condition, limit);

    if (!isToken && appliesHere) {
      const property = HARD_CONSTRAINTS.find((name) =>
        line.startsWith(`${name}:`),
      );
      if (
        property !== undefined &&
        exceeds(/([\d.]+)(px|rem|em)/, line, limit)
      ) {
        offenders.push(line);
      }
      // `minmax(12rem, 1fr)` in a grid template is a floor on a column, which is a demand
      // for width exactly as `min-width` is — and the one that slips past a reviewer.
      const minmax = /minmax\(\s*([\d.]+)(px|rem|em)/.exec(line);
      if (minmax !== null && exceeds(/([\d.]+)(px|rem|em)/, minmax[0], limit)) {
        offenders.push(line);
      }
    }

    depth += count(raw, "{") - count(raw, "}");
    if (condition !== null && depth <= conditionDepth) condition = null;
  }
  return offenders;
}

/** The stylesheet as one declaration, selector or brace per line.
 *
 * Comments go first — a `{` inside prose would throw the brace depth off — and then every
 * `{`, `}` and `;` ends a line. Without this the scan depends on how the file happens to be
 * wrapped, which is a property of the formatter and not of the design.
 */
function declarations(css: string): string[] {
  return css
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/([{};])/g, "$1\n")
    .split("\n");
}

function exceeds(pattern: RegExp, text: string, limit: number): boolean {
  const found = pattern.exec(text);
  if (found === null) return false;
  const pixels = toPixels(found[1] ?? "", found[2] ?? "");
  return pixels !== null && pixels > limit;
}

function count(text: string, character: string): number {
  return text.split(character).length - 1;
}

/** `--viewport-min`, in pixels. The design states the narrowest width it supports; this
 * test holds it to that rather than to a number of its own. */
function narrowestSupported(): number {
  const declared = /--viewport-min:\s*([\d.]+)(px|rem|em)/.exec(tokens);
  if (declared === null) {
    throw new Error(
      "design/tokens.css declares no --viewport-min to test against",
    );
  }
  const pixels = toPixels(declared[1] ?? "", declared[2] ?? "");
  if (pixels === null)
    throw new Error("--viewport-min is not an absolute length");
  return pixels;
}

test("nothing in the layout demands more width than the narrowest supported viewport", () => {
  // Vitest empties CSS imports unless `test.css` is on (vite.config.ts), and an empty
  // stylesheet passes every check below without reading a thing.
  expect(styles.length).toBeGreaterThan(0);
  expect(tooWide(STYLESHEET, narrowestSupported())).toEqual([]);
});

test("the check would catch a rule that is too wide", () => {
  // Without this the assertion above passes on an empty stylesheet, a broken regex, or a
  // scanner that silently stopped matching -- which is the failure mode of every test that
  // asserts an empty list.
  const limit = narrowestSupported();
  expect(tooWide(".panel { min-width: 40rem; }", limit)).toHaveLength(1);
  expect(
    tooWide(".grid { grid-template-columns: minmax(600px, 1fr); }", limit),
  ).toHaveLength(1);
  // And that it does not cry wolf over the two shapes that are legitimately wide.
  expect(tooWide(".page { max-width: 80rem; }", limit)).toEqual([]);
  expect(
    tooWide(
      "@media (min-width: 60rem) {\n  .side { width: 24rem;\n }\n}",
      limit,
    ),
  ).toEqual([]);
});

test("the page scales to the device rather than to a fixed width", () => {
  // The other half of the phone story, and the one a stylesheet cannot fix: without this
  // meta element a mobile browser lays the page out at ~980px and zooms out, which is
  // horizontal scroll arriving from the document rather than from the CSS.
  expect(indexHtml).toMatch(/name="viewport"[^>]*width=device-width/);
});

test("the shell gives the page exactly one main region", () => {
  render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
  // A view that brought its own `<main>` would nest two, which is how a skip link starts
  // landing somewhere other than the content.
  expect(screen.getAllByRole("main")).toHaveLength(1);
});
