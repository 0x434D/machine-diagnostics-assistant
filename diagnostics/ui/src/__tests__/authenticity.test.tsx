/** M6's §1 proofs, for the half of the claim that is about what a reader sees.
 *
 * *"Nothing on screen is drawn from anything the model typed."* Two of its four halves are
 * claims about services and are proved against running ones in
 * `diagnostics/agent/tests/test_authenticity_evidence.py`: that every citation kind
 * resolves against a real endpoint, and that a chart reaching a reader references a tool
 * call the run actually made. The two below are claims about a rendering, and a service
 * cannot make them.
 *
 * **Why these are in `make check` and not behind `make verify`.** The rule
 * `measurements/authenticity/README.md` has applied since M2a is **cost, not category**:
 * the propagation, traceability, consequence and analysis proofs are all in the gate
 * because they run in seconds against something the gate already starts, and §1's own four
 * are held back because they stop and restart containers. These render components into
 * jsdom and take milliseconds. A proof that runs only when somebody deliberately looks is
 * one nobody sees fail.
 *
 * **What no proof in this repository does.** None of this runs in a browser. jsdom performs
 * no layout, so the ISA-101 claim is made on what is rendered and on what the stylesheet
 * declares, and the no-horizontal-scroll claim in `layout.test.tsx` is made on the
 * stylesheet alone. Closing that needs a browser driver, which is a dependency nobody has
 * agreed to; it is written down in `measurements/authenticity/README.md` rather than left
 * to be inferred from a green pipeline.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import analysisContract from "../generated/analysis.ts?raw";
import apiSource from "../api.ts?raw";
import tokens from "../design/tokens.css?raw";
import sheet from "../styles.css?raw";
import { App, AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { CitationChip } from "../CitationChip";
import { ExchangeProvider } from "../citations/exchange";
import { PlantStatusBanner } from "../plant/PlantStatusBanner";
import { ROUTES } from "../shell/routes";
import { StateBadge } from "../design/StateBadge";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import { CATEGORIES, ENCODINGS, statesIn } from "../design/stateCategory";
import type { Citation, CitationWindow } from "../generated/answer";
import type { Trace } from "../api";

const TOKEN = "dev-token-123";

/** A window whose label carries no digits.
 *
 * Every chart is titled with the interval the pipeline stamped, and the exactness proof
 * below counts the numbers printed on a chart. A label reading "01:00 – 02:00" would put
 * four of them there that are not data, and the claim being made is about the data.
 */
const UNNUMBERED_WINDOW: CitationWindow = {
  from_ts: "2026-09-12T01:00:00Z",
  to_ts: "2026-09-12T02:00:00Z",
  label: "the hour before last",
};

const EXCHANGE = {
  sessionId: "1f3f2b6e-0000-4000-8000-000000000009",
  seq: 2,
};

function traceOf(result: Record<string, unknown>): Trace {
  return {
    sops_loaded: ["CORE-01"],
    budget: { tool_turns: 1, tool_turns_limit: 6 },
    timings: { total_ms: 100, model_ms: 80, tools_ms: 20 },
    tool_calls: [
      {
        id: "call_inspection_stats",
        name: "inspection_stats",
        arguments: {},
        result,
        duration_ms: 12,
        failed: false,
      },
    ],
  };
}

function answering(body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

function openChart(citation: Citation) {
  const view = render(
    <AuthProvider>
      <MemoryRouter>
        <ExchangeProvider exchange={EXCHANGE}>
          <CitationChip citation={citation} />
        </ExchangeProvider>
      </MemoryRouter>
    </AuthProvider>,
  );
  within(view.container)
    .getAllByRole("button")[0]
    ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  return view;
}

async function drawn(): Promise<SVGSVGElement> {
  const canvas = await screen.findByTestId("chart-canvas");
  return await vi.waitFor(() => {
    const found = canvas.querySelector("svg");
    if (found === null) throw new Error("no chart was drawn");
    return found as SVGSVGElement;
  });
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, TOKEN);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

// --- §7.4: the picture is a reading of a stored tool result and of nothing else ----------

const PARETO: Citation = {
  kind: "chart",
  chart_type: "pareto",
  source: "call_inspection_stats",
  options: { series: "rows", x: "defect_class", y: "count" },
  window: UNNUMBERED_WINDOW,
};

test("the same chart citation over two stored results draws two different pictures", async () => {
  // §7.4's claim stated as the thing that would fail if it were a facade. That a chart
  // "shows what the tool call measured" is satisfied by a renderer that happens to draw
  // the right thing once; that the *same citation object* draws a different picture when
  // the stored result changes, and only then, is satisfied by nothing except the values
  // coming from the result.
  answering(
    traceOf({
      rows: [
        { defect_class: "misalignment", count: 18 },
        { defect_class: "contamination", count: 7 },
      ],
    }),
  );
  const first = openChart(PARETO);
  const before = (await drawn()).textContent ?? "";
  first.unmount();
  vi.unstubAllGlobals();

  answering(
    traceOf({
      rows: [
        { defect_class: "delamination", count: 41 },
        { defect_class: "porosity", count: 2 },
      ],
    }),
  );
  openChart(PARETO);
  const after = (await drawn()).textContent ?? "";

  expect(before).toContain("misalignment");
  expect(before).not.toContain("delamination");
  expect(after).toContain("delamination");
  // The important half: what the first result held is gone. A renderer holding anything of
  // its own — a cached series, a default, a value read off the citation — would leave it.
  expect(after).not.toContain("misalignment");
});

test("every figure printed on a chart is one the stored tool result holds", async () => {
  // Counted rather than sampled, on the one type in §7.4's vocabulary that prints its
  // figures rather than putting them on an axis: a summary tile is a number and its label,
  // with no scale, so every numeral drawn is a datum or it came from somewhere else.
  //
  // The built-in types with axes are not checked this way and could not be: an axis tick
  // is Vega's own arithmetic over the data's domain, so "2,000" can legitimately appear on
  // a chart whose largest value is 1,840. What that arithmetic is a function of is the
  // data, which is what the mutation above measures.
  const tiles = [
    { metric: "parts", value: 613 },
    { metric: "rejects", value: 29 },
  ];
  answering(traceOf({ tiles }));

  openChart({
    kind: "chart",
    chart_type: "summary_tiles",
    source: "call_inspection_stats",
    options: { series: "tiles", x: "metric", y: "value" },
    window: UNNUMBERED_WINDOW,
  });
  const svg = await drawn();

  const measured = new Set(tiles.map((tile) => String(tile.value)));
  const printed = Array.from(svg.querySelectorAll("text"))
    .map((node) => node.textContent ?? "")
    .filter((text) => /\d/.test(text));

  expect(printed.length).toBeGreaterThan(0);
  for (const figure of printed) {
    expect(
      measured,
      `${figure} is on the chart and not in the tool result`,
    ).toContain(figure.replace(/[,\s]/g, ""));
  }
});

// --- §7.3: every address this application asks for is one the contract declares ----------

/** The path templates the analysis service serves, out of the generated contract.
 *
 * `src/generated/analysis.ts` is written by `openapi-typescript` from
 * `contracts/analysis.openapi.yaml` and the gate regenerates it and fails on a diff, so
 * this is the contract itself rather than a reading of it.
 */
function declaredPaths(): string[] {
  const found = Array.from(
    analysisContract.matchAll(/^ {4}"(\/[^"]*)":/gm),
  ).map((match) => match[1] as string);
  expect(
    found.length,
    "the generated contract declares no paths",
  ).toBeGreaterThan(0);
  return found;
}

/** Every path `api.ts` asks the analysis service for, as it writes them.
 *
 * Read off the module's own source rather than observed through a render: what has to be
 * checked is *every* address this application can ask for, and a render only exercises the
 * ones a test remembered to open. §7.3's promise is that the frontend cannot drift from the
 * API — the types cannot, because they are generated, and the addresses are the half that
 * is still hand-written.
 *
 * `getAnalysis` is the one door: every other way of reaching that service would have to
 * write `/api/analysis` itself, and the test below is what says nothing does.
 */
function requestedPaths(): string[] {
  return Array.from(
    apiSource.matchAll(/getAnalysis<[^>]*>\(\s*(`[^`]*`|"[^"]*")/g),
  ).map((match) =>
    (match[1] as string)
      .slice(1, -1)
      // A path a function assembles from its arguments: the interpolations are the
      // parameters, and the contract writes them as `{name}`.
      .replace(/\$\{[^}]*\}/g, "{param}"),
  );
}

test("every analysis address this application builds is a path the contract serves", () => {
  // The seam between the two halves of §7.3's guarantee. The *types* are generated from
  // `contracts/`, so a renamed field breaks the build; the *addresses* are written out in
  // `api.ts`, so a renamed path breaks nothing until a reader clicks a citation and gets a
  // 404 that reads exactly like an id the agent invented — which is the one failure §6.5
  // exists to make distinguishable.
  const declared = declaredPaths().map((path) =>
    path.replace(/\{[^}]*\}/g, "{param}"),
  );
  const asked = requestedPaths();

  // Fifteen of the contract's seventeen operations are reachable from this application, so
  // a floor rather than an equality — and a floor at all, because a regex that matched
  // nothing would iterate nothing and pass loudest of all.
  expect(asked.length, "api.ts builds no analysis addresses").toBeGreaterThan(
    10,
  );
  for (const path of asked) {
    expect(
      declared,
      `api.ts asks for ${path}, which the contract does not declare`,
    ).toContain(path);
  }
});

test("no module but api.ts writes a service address", () => {
  // What makes the enumeration above complete. A component that built `/api/analysis/...`
  // for itself would be an address the contract never sees, and §7.3's whole claim is that
  // the frontend cannot drift from the API.
  const modules = import.meta.glob("../**/*.{ts,tsx}", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  const written = Object.entries(modules)
    // The suites themselves assert on addresses, which is them doing their job.
    .filter(([path]) => !/\.test\.tsx?$/.test(path))
    .filter(([, source]) => /["'`]\/api\//.test(source))
    .map(([path]) => path);

  expect(Object.keys(modules).length).toBeGreaterThan(10);
  expect(written).toEqual(["../api.ts"]);
});

/** Every address this application builds for the agent, written out.
 *
 * **The weakest check in this file, and it says so.** `contracts/` holds the analysis
 * service's OpenAPI document and the agent's three *payload* schemas — the answer, the
 * trace and the feedback — and no path document for the agent, so there is nothing
 * generated to hold these against the way the test above holds the analysis paths. A list
 * written here cannot catch a route the agent renamed; what it catches is a fifth address
 * appearing without anyone noticing that it, too, is checked by nothing.
 */
const AGENT_ADDRESSES = ["/ask", "/sessions/{param}"];
const AGENT_SUFFIXES = ["/messages/{param}", "/trace", "/feedback"];

test("the addresses this application builds for the agent are the ones written down", () => {
  const asked = Array.from(apiSource.matchAll(/\$\{AGENT\}([^`]*)`/g)).map(
    (match) => (match[1] as string).replace(/\$\{[^}]*\}/g, "{param}"),
  );

  expect(asked.length).toBeGreaterThan(0);
  expect([...new Set(asked)].toSorted()).toEqual(
    [...AGENT_ADDRESSES].toSorted(),
  );
  // `messageAt` continues the second of those in a separate fragment, so the tail is
  // checked as a tail. That this is what a check of an agent address looks like here is
  // the point being recorded.
  for (const suffix of AGENT_SUFFIXES) {
    expect(
      apiSource.replace(/\$\{[^}]*\}/g, "{param}"),
      `api.ts no longer builds ${suffix}`,
    ).toContain(suffix);
  }
  // And the analysis contract is not quietly the place they are declared, which is the one
  // reading of the sentence above that would make it untrue.
  expect(analysisContract).not.toContain("/messages/");
});

// --- §15 and ISA-101: colour is never the only channel -----------------------------------

/** Every `--category-*` colour the token file declares, tints and the sentinel excluded.
 *
 * Enumerated from the stylesheet rather than from `CATEGORIES`, deliberately and in that
 * direction: the failure this guards against is a sixth colour being added to the palette
 * for a sixth kind of state, with no glyph and no word to carry it when the colour is
 * gone. Read from the other side, that colour would never be noticed.
 */
function paletteCategories(): string[] {
  const declared = new Set(
    Array.from(tokens.matchAll(/--category-([a-z-]+)\s*:/g))
      .map((match) => match[1] as string)
      .filter((name) => !name.endsWith("-tint") && name !== "unknown"),
  );
  return Array.from(declared).toSorted();
}

test("every category the palette paints reads without its colour", () => {
  const painted = paletteCategories();

  expect(painted).toEqual([...CATEGORIES].toSorted());
  for (const category of painted) {
    const encoding = ENCODINGS[category as (typeof CATEGORIES)[number]];
    expect(encoding, `--category-${category} has no encoding`).toBeDefined();
    expect(encoding.glyph).not.toBe("");
    expect(encoding.label).not.toBe("");
  }
  // And the two non-colour channels are as distinguishing as the colour is: a shared glyph
  // or a shared label makes two categories identical on a greyscale print.
  expect(new Set(CATEGORIES.map((c) => ENCODINGS[c].glyph)).size).toBe(
    CATEGORIES.length,
  );
  expect(new Set(CATEGORIES.map((c) => ENCODINGS[c].label)).size).toBe(
    CATEGORIES.length,
  );
});

/** Which surfaces this proof renders and reads the second channel off.
 *
 * The check below is that this list is the whole of what the stylesheet paints a category
 * colour on. A view that grows its own coloured rectangle out of `--category-*` fails here
 * until somebody either renders it in this file or stops painting it — which is the
 * enumeration ISA-101 needs, because the failure is always one more surface rather than
 * the one everybody remembered.
 */
const COLOURED_SURFACES = ["contradiction", "plant-status", "state-badge"];

/** The stylesheet with its comments removed.
 *
 * Not tidiness: a comment reading "§6.5's contradiction" holds `.5`, which every selector
 * pattern below would read as a class named `5`. The first draft of this file did.
 */
const RULES = sheet.replace(/\/\*[\s\S]*?\*\//g, "");

/** Every rule that paints a `--category-*` colour, as `[selector, declarations]`.
 *
 * Innermost rules only, which is what the pattern gives: an `@media` wrapper's body holds
 * braces and cannot match, so its own condition is never mistaken for a selector.
 */
function categoryRules(): [string, string][] {
  return Array.from(RULES.matchAll(/([^{}]+)\{([^{}]*)\}/g))
    .filter((match) => (match[2] as string).includes("var(--category-"))
    .map((match) => [(match[1] as string).trim(), match[2] as string]);
}

test("the stylesheet paints a category colour on no surface this proof does not read", () => {
  const surfaces = new Set<string>();
  for (const [selector] of categoryRules()) {
    for (const part of selector.split(",")) {
      const named = /\.([a-z][a-z0-9-]*)/.exec(part.trim());
      if (named !== null) surfaces.add(named[1] as string);
    }
  }

  expect(surfaces.size).toBeGreaterThan(0);
  expect([...surfaces].toSorted()).toEqual([...COLOURED_SURFACES].toSorted());
});

test("the third surface the enumeration found says in words what its colour means", async () => {
  // §6.5's contradiction banner, and the reason the surfaces above are enumerated rather
  // than listed: `StateBadge` was built with three channels and tested for them, and then
  // two more things arrived that paint from the same five hues. This one borrows
  // `held-by-own-fault` — the colour that means *go and look at this* — and it has to
  // carry the same sentence the colour does.
  const { ContradictionBanner } = await import("../answer/ContradictionBanner");

  render(
    <ContradictionBanner
      contradiction={{
        derived_root: "S1",
        agent_root: "S2",
        reasoning:
          "the clamp force fell 10 sigma before the feeder ever starved",
      }}
    />,
  );

  const banner = screen.getByRole("region");
  expect(banner).toHaveClass("contradiction");
  // The heading, with everything that is not a letter taken out — which is what is left of
  // a banner whose word was dropped and whose warning glyph was not. A coloured rectangle
  // with a ⚠ in it is the colour being the only channel wearing a symbol.
  const heading = within(banner).getByRole("heading");
  expect((heading.textContent ?? "").replace(/[^\p{L}]/gu, "")).not.toBe("");
  expect(banner).toHaveTextContent("S1");
  expect(banner).toHaveTextContent("S2");
});

test("a state badge says which category it is in words and in a shape", () => {
  for (const category of CATEGORIES) {
    const state = statesIn(category)[0];
    if (state === undefined) throw new Error(`${category} maps no state`);

    const { container, unmount } = render(<StateBadge state={state} />);
    const painted = container.querySelector(`[data-category="${category}"]`);

    expect(
      painted,
      `${category} is not the badge's own data attribute`,
    ).not.toBeNull();
    expect(painted).toHaveTextContent(ENCODINGS[category].glyph);
    expect(painted).toHaveTextContent(ENCODINGS[category].label);
    unmount();
  }
});

/** The gateway states `styles.css` gives a category colour to, out of the stylesheet. */
function colouredPlantStates(): string[] {
  return Array.from(
    new Set(
      sheet
        .split("}")
        .filter((block) => block.includes("var(--category-"))
        .flatMap((block) =>
          Array.from(
            (block.split("{")[0] ?? "").matchAll(
              /\.plant-status\[data-state="([a-z_]+)"\]/g,
            ),
          ).map((match) => match[1] as string),
        ),
    ),
  ).toSorted();
}

test("the plant banner reads differently in every state it paints a category colour for", async () => {
  // The second surface, and the one that shows why this is enumerated: `StateBadge` was
  // built with three channels and tested for them, and then a banner arrived that paints
  // from the same palette. Each of its readings has to be a word of its own — a banner
  // that says "the gateway says something" in five hues tells a reader nothing at all.
  const seen = new Map<string, string>();

  for (const state of colouredPlantStates()) {
    answering({
      state,
      lastEventSourceTs: "2026-09-11T03:14:00",
      queueDepth: 0,
      backfillProgress: 0,
      overflowCount: 0,
      rowsWritten: 4210,
      historyAvailableFrom: "2026-09-10T18:00:00",
      clockAvailable: true,
    });

    const { unmount } = render(
      <AuthProvider>
        <PlantStatusBanner />
      </AuthProvider>,
    );
    // One state at a time, and it has to be: each iteration replaces the global fetch
    // stub, so two renders in flight would read each other's gateway.
    // oxlint-disable-next-line no-await-in-loop
    const banner = await waitFor(() => {
      const found = screen.getByRole("status");
      if (found.getAttribute("data-state") !== state) {
        throw new Error(
          `the banner still reads ${found.getAttribute("data-state")}`,
        );
      }
      return found;
    });

    // The *label*, not the whole banner. Every reading also carries a sentence and the
    // gateway's own word, so comparing the full text would pass a banner whose five
    // readings all said "Connected" in the one place a reader actually looks — and that
    // is the colour being the only channel, arrived at through a word that lies.
    const label =
      banner.querySelector(".plant-status__label")?.textContent ?? "";
    const glyph =
      banner.querySelector(".plant-status__glyph")?.textContent ?? "";

    expect(label.trim(), `${state} renders no reading`).not.toBe("");
    expect(glyph.trim(), `${state} renders no glyph`).not.toBe("");
    for (const [other, already] of seen) {
      expect(label, `${state} and ${other} read alike`).not.toBe(already);
    }
    seen.set(state, label);
    unmount();
    vi.unstubAllGlobals();
  }

  expect(seen.size).toBe(colouredPlantStates().length);
});

// --- §10.5: an unauthenticated visitor gets the login screen and no data ------------------

test("with no token the application mounts no view and asks the services nothing", async () => {
  // §1.8 closed this for the APIs at M5, endpoint by endpoint. This is the same claim for
  // the application a person opens, and it is a different one: the services refusing an
  // anonymous request is what protects the data, and this is what stops the screen showing
  // a shell full of failed requests — and, at the address a citation link points at, a view
  // that never checked.
  //
  // Enumerated over `ROUTES` rather than over the two views this test remembered, so a view
  // added tomorrow is covered by construction. `App` puts the login *in front of* the
  // router for exactly that reason, and this is what would fail if it moved inside.
  window.localStorage.clear();
  const asked = vi.fn(() => Promise.reject(new Error("nothing may be asked")));
  vi.stubGlobal("fetch", asked);

  render(<App />);

  expect(
    await screen.findByRole("button", { name: /sign in/i }),
  ).toBeInTheDocument();
  for (const route of ROUTES) {
    if (route.nav === null) continue;
    expect(
      screen.queryByRole("link", { name: route.nav }),
      `${route.path} is reachable without a token`,
    ).toBeNull();
  }
  // Not one request, to any service, from any component — the banner included. A screen
  // that asked and rendered the 401 would be telling an unauthenticated visitor something
  // about the plant.
  expect(asked).not.toHaveBeenCalled();
});

test("the same views mount once a token is held, so the refusal above is the token's doing", async () => {
  // Without this the proof above is satisfied by an application that renders nothing at
  // all, which is the shape a broken build has.
  answering({
    state: "live",
    clockAvailable: true,
    rowsWritten: 0,
    queueDepth: 0,
  });

  render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );

  const links = await Promise.all(
    ROUTES.filter((route) => route.nav !== null).map(
      async (route) =>
        await screen.findByRole("link", { name: route.nav ?? "" }),
    ),
  );
  for (const link of links) expect(link).toBeInTheDocument();
});
