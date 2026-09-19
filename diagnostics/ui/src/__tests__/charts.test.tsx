/** §7.4: a chart is a citation, not a decoration beside one.
 *
 * *"The data always comes from a verified tool result. The specification references a tool
 * call by id; it never carries values the model typed. This kills the classic failure where
 * a model draws a confident chart from invented figures — and a wrong chart reads far more
 * authoritatively than a wrong sentence."*
 *
 * What is asserted here is what a reader sees, which for a chart means three things: that
 * each of the six built-in types draws from the rows of a referenced tool call, that the
 * free-form fallback draws a valid specification and refuses one carrying its own figures,
 * and that **every way a chart can fail produces a sentence rather than an empty set of
 * axes** — an empty chart reads as zero, which is the same wrong answer arrived at by
 * omission.
 *
 * §7.4 singles the fallback out: *"the fallback path is the least-exercised code in the
 * frontend precisely because it fires rarely; it needs its own tests rather than relying on
 * incidental use."* It has them below, and it also shares the rendering path the six use.
 */
// Vite's `?raw` suffix reads the generated file as text, so the enumeration at the bottom
// runs against the union `json2ts` wrote rather than against a copy of it kept here.
import SOURCE from "../generated/answer.ts?raw";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { AuthProvider } from "../AuthContext";
import { CitationChip } from "../CitationChip";
import { ExchangeProvider } from "../citations/exchange";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import { carriesData, type DataFree } from "../charts/dataFree";
import { ENCODINGS } from "../design/stateCategory";
import type { Trace } from "../api";
import type {
  Citation,
  CitationWindow,
  ChartOptions,
} from "../generated/answer";

const TOKEN = "dev-token-123";
const EXCHANGE = { sessionId: "1f3f2b6e-0000-4000-8000-000000000042", seq: 3 };

const WINDOW: CitationWindow = {
  from_ts: "2026-09-12T01:00:00Z",
  to_ts: "2026-09-12T02:00:00Z",
  label: "the hour 2026-09-12 01:00 – 02:00 UTC",
};

// --- the trace this exchange's charts are drawn from ---------------------------------------
//
// One call per built-in type, each shaped like the analysis endpoint it stands for. Typed
// against the generated trace contract, so a field the agent renames breaks these at compile
// time rather than leaving a chart quietly empty.

function made(
  id: string,
  name: string,
  result: Record<string, unknown>,
  failed = false,
): Trace["tool_calls"] extends (infer T)[] | undefined ? T : never {
  return {
    id,
    name,
    arguments: { group_by: "defect_class" },
    result,
    duration_ms: 12,
    failed,
  };
}

const TRACE: Trace = {
  sops_loaded: ["CORE-01"],
  budget: { tool_turns: 2, tool_turns_limit: 6 },
  timings: { total_ms: 900, model_ms: 700, tools_ms: 200 },
  tool_calls: [
    made("call_signal_trend", "signal_trend", {
      points: [
        { at: "2026-09-12T01:00:00Z", value: 904.5 },
        { at: "2026-09-12T01:30:00Z", value: 911.2 },
        { at: "2026-09-12T02:00:00Z", value: 918.7 },
      ],
    }),
    made("call_list_stops", "list_stops", {
      stops: [
        { from_ts: "2026-09-12T01:20:00Z", to_ts: "2026-09-12T01:26:00Z" },
      ],
    }),
    made("call_get_stop", "get_stop", {
      timeline: [
        {
          station: "S2",
          state: "Held",
          from_ts: "2026-09-12T01:20:00Z",
          to_ts: "2026-09-12T01:26:00Z",
        },
        {
          station: "S4",
          state: "Suspended",
          from_ts: "2026-09-12T01:21:00Z",
          to_ts: "2026-09-12T01:26:00Z",
        },
      ],
    }),
    made("call_inspection_stats", "inspection_stats", {
      defect_classes: [
        { defect_class: "misalignment", count: 18 },
        { defect_class: "contamination", count: 7 },
        { defect_class: "short_fill", count: 3 },
      ],
    }),
    made("call_by_carrier", "inspection_stats", {
      groups: [
        { carrier: "3", defect_class: "misalignment", count: 4 },
        { carrier: "7", defect_class: "misalignment", count: 14 },
        { carrier: "7", defect_class: "contamination", count: 2 },
      ],
    }),
    made("call_by_time", "inspection_stats", {
      buckets: [
        { at: "2026-09-12T01:00:00Z", reject_rate: 0.02 },
        { at: "2026-09-12T01:30:00Z", reject_rate: 0.08 },
      ],
    }),
    made("call_line_status", "line_status", {
      tiles: [
        { metric: "parts", value: 600 },
        { metric: "rejects", value: 28 },
      ],
    }),
    made(
      "call_that_failed",
      "inspection_patterns",
      { error: true, detail: "HTTPError: connection refused" },
      true,
    ),
  ],
};

function chart(
  chart_type: Citation["chart_type"],
  options: ChartOptions,
): Citation {
  return {
    kind: "chart",
    chart_type,
    source:
      options.bands === undefined
        ? SOURCES[String(chart_type)]
        : SOURCES.timeseries,
    options,
    window: WINDOW,
  };
}

/** Which call each built-in is drawn from, so the table below reads as a vocabulary. */
const SOURCES: Record<string, string> = {
  timeseries: "call_signal_trend",
  state_gantt: "call_get_stop",
  pareto: "call_inspection_stats",
  stacked_bar: "call_by_carrier",
  rate_over_time: "call_by_time",
  summary_tiles: "call_line_status",
  vega_lite: "call_inspection_stats",
};

/** §7.4's six, each as a reading of one of the calls above. A `chart_type` that stopped
 * having an entry here would fail the enumeration test at the bottom of this file. */
const BUILT_INS: Record<string, ChartOptions> = {
  timeseries: { series: "points", x: "at", y: "value" },
  state_gantt: {
    series: "timeline",
    x: "from_ts",
    end: "to_ts",
    y: "station",
    colour: "state",
  },
  pareto: { series: "defect_classes", x: "defect_class", y: "count" },
  stacked_bar: {
    series: "groups",
    x: "carrier",
    y: "count",
    colour: "defect_class",
  },
  rate_over_time: { series: "buckets", x: "at", y: "reject_rate" },
  summary_tiles: { series: "tiles", x: "metric", y: "value" },
};

// --- opening one -------------------------------------------------------------------------

function stubTrace(trace: Trace = TRACE): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify(trace), {
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function open(citation: Citation): HTMLElement {
  const view = render(
    <AuthProvider>
      <ExchangeProvider exchange={EXCHANGE}>
        <CitationChip citation={citation} />
      </ExchangeProvider>
    </AuthProvider>,
  );
  fireEvent.click(
    within(view.container).getAllByRole("button")[0] as HTMLElement,
  );
  return view.container;
}

/** How many marks the chart drew from its data.
 *
 * `role-mark` is Vega's own group for the marks a specification asked for, as against the
 * axes, gridlines and legends it draws around them. Counting those is the difference
 * between "a chart" and "a complete set of axes with nothing between them", which is the
 * picture this whole file is about — and the one that passes a test asserting an `<svg>`.
 */
function marks(svg: SVGSVGElement): number {
  return Array.from(svg.querySelectorAll("g.role-mark")).reduce(
    (total, group) => total + group.children.length,
    0,
  );
}

/** The drawn chart, once Vega has run. */
async function drawn(): Promise<SVGSVGElement> {
  const canvas = await screen.findByTestId("chart-canvas");
  const svg = await vi.waitFor(() => {
    const found = canvas.querySelector("svg");
    if (found === null) throw new Error("no chart was drawn");
    return found;
  });
  return svg as SVGSVGElement;
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, TOKEN);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

// --- the six ------------------------------------------------------------------------------

/** How many rows the referenced call holds for each type, so "it drew something" can be
 * "it drew the rows that were measured". */
const ROWS: Record<string, number> = {
  timeseries: 3,
  state_gantt: 2,
  pareto: 3,
  stacked_bar: 3,
  rate_over_time: 2,
  summary_tiles: 2,
};

test.each(Object.keys(BUILT_INS))(
  "a %s chart draws a mark for every row of the tool call it references",
  async (type) => {
    stubTrace();

    open(
      chart(type as Citation["chart_type"], BUILT_INS[type] as ChartOptions),
    );

    const svg = await drawn();
    // Marks, and not merely axes: a chart that compiled and drew nothing has axes exactly
    // like a real one, and that picture is the whole of what §7.4 is about.
    expect(marks(svg)).toBeGreaterThanOrEqual(ROWS[type] ?? 1);
    expect(await screen.findByTestId("evidence-panel")).toHaveAttribute(
      "data-outcome",
      "open",
    );
  },
);

/** A value out of the referenced tool result that has to be legible on the finished chart.
 *
 * Counting marks says something was drawn; this says what was drawn came from the call. The
 * `timeseries` is absent because its axes carry no categorical label to check — the band
 * test below is what holds it. */
const SHOWS: [string, string][] = [
  ["state_gantt", "S2"],
  ["pareto", "misalignment"],
  ["stacked_bar", "contamination"],
  ["summary_tiles", "600"],
  // The distinguishing behaviour of a rate: a fraction the service reports as 0.08 is read
  // off the axis as 8 %, because the other is a number a reader converts in their head.
  ["rate_over_time", "%"],
];

test.each(SHOWS)(
  "a %s chart shows what the tool call measured",
  async (type, shown) => {
    stubTrace();

    open(
      chart(type as Citation["chart_type"], BUILT_INS[type] as ChartOptions),
    );
    const svg = await drawn();

    expect(svg.textContent).toContain(shown);
  },
);

test.each(Object.keys(BUILT_INS))(
  "a %s chart says which tool call it was drawn from",
  async (type) => {
    // §7.4's claim is about provenance, so the provenance is on the screen: a reader can
    // check the picture against the trace §7.2 already shows without counting entries.
    stubTrace();

    const container = open(
      chart(type as Citation["chart_type"], BUILT_INS[type] as ChartOptions),
    );
    await drawn();

    expect(container).toHaveTextContent("Drawn from the tool call");
    expect(container).toHaveTextContent(
      TRACE.tool_calls?.find((call) => call.id === SOURCES[type])?.name ?? "",
    );
  },
);

test("a chart is labelled with the window its claim was made over", async () => {
  // The window is the one the pipeline stamped (§7.3), never one the screen picked. A
  // chart of true figures on a ruler nobody chose is the same failure with the invention
  // moved from the values to the axis.
  stubTrace();

  open(chart("pareto", BUILT_INS.pareto as ChartOptions));
  const svg = await drawn();

  expect(svg.textContent).toContain(WINDOW.label);
});

test("a timeseries shades the periods its second tool call names", async () => {
  // §7.4's first type: "a process value with stop and alarm periods shaded". The bands are
  // a second *reference* and not a second set of values.
  stubTrace();

  open({
    kind: "chart",
    chart_type: "timeseries",
    source: "call_signal_trend",
    options: {
      series: "points",
      x: "at",
      y: "value",
      bands: "call_list_stops",
      bands_series: "stops",
      bands_x: "from_ts",
      bands_end: "to_ts",
    },
    window: WINDOW,
  });

  const svg = await drawn();
  // Two layers of marks rather than one: the band rectangle behind the line.
  expect(svg.querySelectorAll("g.mark-rect, g.mark-line").length).toBe(2);
});

test("a state Gantt speaks the application's colour language, and not only in colour", async () => {
  // §15 and ISA-101, on a chart: the five categories rather than fifteen PackML states,
  // and the glyph on the mark as well as the written category in the legend — so "this
  // station is the problem" survives a greyscale print.
  stubTrace();

  open(chart("state_gantt", BUILT_INS.state_gantt as ChartOptions));
  const svg = await drawn();

  const text = svg.textContent ?? "";
  expect(text).toContain(ENCODINGS["held-by-own-fault"].label);
  expect(text).toContain(ENCODINGS["waiting-on-others"].label);
  expect(text).toContain(ENCODINGS["held-by-own-fault"].glyph);
  // And the fifteen state names are not the colour channel: `Held` and `Aborted` are one
  // category, which is the distinction §5.4's derivation rests on.
  expect(text).not.toContain("Suspending");
});

test("a chart is painted in the design tokens rather than a library's palette", async () => {
  // `design/tokens.css`: "nothing else in this application writes a colour". A charting
  // library ships a palette, the palette looks fine, and the screen quietly grows a second
  // language for station state.
  stubTrace();
  const accent = getComputedStyle(document.documentElement)
    .getPropertyValue("--accent")
    .trim();

  open(chart("pareto", BUILT_INS.pareto as ChartOptions));
  const svg = await drawn();

  expect(accent).not.toBe("");
  expect(svg.outerHTML).toContain(accent);
});

// --- §7.4's free-form fallback --------------------------------------------------------------

const FALLBACK: ChartOptions = {
  series: "defect_classes",
  spec: {
    mark: "bar",
    encoding: {
      x: { field: "defect_class", type: "nominal" },
      y: { field: "count", type: "quantitative" },
    },
  },
};

test("the fallback renders a valid Vega-Lite specification", async () => {
  stubTrace();

  open(chart("vega_lite", FALLBACK));
  const svg = await drawn();

  expect(svg.querySelectorAll("g.mark-rect").length).toBeGreaterThan(0);
  expect(svg.textContent).toContain("misalignment");
});

test("the fallback is bound to the tool result and not to anything it brought itself", async () => {
  // The whole of §7.4 in one assertion: the model wrote the marks and the encoding, and
  // the values under them came out of the referenced call.
  stubTrace();

  open(chart("vega_lite", FALLBACK));
  const container = await screen.findByTestId("chart-canvas");

  expect(await drawn()).toBeTruthy();
  expect(container.parentElement).toHaveTextContent("misalignment");
});

test("a fallback carrying its own values is refused rather than drawn", async () => {
  // The classic failure, arriving through the one door a model's own specification comes
  // in by. The agent refuses this when the answer is built; this is the same rule at the
  // last boundary before a reader sees a picture.
  stubTrace();

  open({
    kind: "chart",
    chart_type: "vega_lite",
    source: "call_inspection_stats",
    options: {
      series: "defect_classes",
      spec: {
        mark: "bar",
        data: { values: [{ defect_class: "invented", count: 99 }] },
      },
    },
    window: WINDOW,
  });

  const refusal = await screen.findByTestId("citation-missing");
  expect(refusal).toHaveTextContent("carries its own values");
  expect(screen.queryByTestId("chart-canvas")).toBeNull();
});

test("a specification Vega-Lite cannot compile says so instead of leaving a blank", async () => {
  // The fallback is the one chart whose shape a model wrote, so it is the one that can be
  // invalid. A blank region under a heading reads as "nothing happened".
  stubTrace();

  open({
    kind: "chart",
    chart_type: "vega_lite",
    source: "call_inspection_stats",
    options: { series: "defect_classes", spec: { mark: { type: 17 } } },
    window: WINDOW,
  });

  expect(await screen.findByTestId("chart-failure")).toHaveTextContent(
    "could not be drawn",
  );
});

test("data hidden below the top level of a specification is found", () => {
  // Vega-Lite nests: a `layer` holds whole specifications of its own, and a top-level check
  // would pass a chart whose second layer carried the invented numbers.
  expect(
    carriesData({
      layer: [{ mark: "line" }, { mark: "point", data: { values: [] } }],
    }),
  ).toBe("layer.[1].data");
  expect(carriesData({ datasets: { invented: [] } })).toBe("datasets");
  expect(
    carriesData({ mark: "bar", encoding: { x: { field: "a" } } }),
  ).toBeNull();
});

test("a specification carrying data is not a type this application can write", () => {
  // The other half of the same claim, and the half a test cannot assert at run time: the
  // compiler refuses it. `@ts-expect-error` fails the build if the line ever compiles, so
  // this check cannot rot into a comment.
  const honest: DataFree = {
    mark: "bar",
    encoding: { x: { field: "defect_class", type: "nominal" } },
  };
  // @ts-expect-error §7.4: a chart specification never carries values.
  const invented: DataFree = { mark: "bar", data: { values: [{ a: 1 }] } };

  expect(honest.mark).toBe("bar");
  expect(invented).toBeTruthy();
});

// --- every way a chart can fail produces a sentence ------------------------------------------

test("a chart naming a tool call that never happened renders the failure", async () => {
  // Not an empty axis. §6.5 should have removed this citation before the answer shipped, so
  // reaching here means the service sent one it had already checked — which is worth saying
  // out loud rather than drawing around.
  stubTrace();

  open({
    kind: "chart",
    chart_type: "pareto",
    source: "call_that_never_happened",
    options: BUILT_INS.pareto as ChartOptions,
    window: WINDOW,
  });

  const refusal = await screen.findByTestId("citation-missing");
  expect(refusal).toHaveTextContent("no such call");
  expect(screen.queryByTestId("chart-canvas")).toBeNull();
});

test("a chart may not be drawn from a tool call that failed", async () => {
  // §6.8 hands a tool failure back to the model as a result, so a failed call *has* a
  // result — the error object. Drawing it would put a chart of a connection refusal on the
  // same axes a real one would use.
  stubTrace();

  open({
    kind: "chart",
    chart_type: "pareto",
    source: "call_that_failed",
    options: BUILT_INS.pareto as ChartOptions,
    window: WINDOW,
  });

  expect(await screen.findByTestId("citation-missing")).toHaveTextContent(
    "failed",
  );
});

test("a path that holds no rows is a sentence and not an empty chart", async () => {
  stubTrace();

  open({
    kind: "chart",
    chart_type: "pareto",
    source: "call_inspection_stats",
    options: { series: "nowhere", x: "defect_class", y: "count" },
    window: WINDOW,
  });

  expect(await screen.findByTestId("citation-missing")).toHaveTextContent(
    "holds nothing at nowhere",
  );
});

test("a field the rows do not carry is a sentence and not an empty chart", async () => {
  // This is the one that would otherwise pass every check and draw a complete set of axes
  // with nothing between them — Vega-Lite reports no error for an encoding that names a
  // field the data has not got.
  stubTrace();

  open({
    kind: "chart",
    chart_type: "pareto",
    source: "call_inspection_stats",
    options: { series: "defect_classes", x: "defect_class", y: "invented" },
    window: WINDOW,
  });

  const refusal = await screen.findByTestId("citation-missing");
  expect(refusal).toHaveTextContent("reads invented");
  expect(screen.queryByTestId("chart-canvas")).toBeNull();
});

test("a trace this reader may not have is the service's refusal, not an empty chart", async () => {
  // §10.5: a session belongs to whoever opened it. The panel reports the refusal the way
  // every other citation kind does rather than rendering a picture of nothing.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ detail: "this session belongs to another user" }),
          {
            status: 403,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    ),
  );

  open(chart("pareto", BUILT_INS.pareto as ChartOptions));

  const panel = await screen.findByTestId("evidence-panel");
  expect(panel).toHaveAttribute("data-outcome", "failed");
  expect(panel).toHaveTextContent("another user");
});

test("a chart shown outside the exchange that made its tool call says so", async () => {
  // A tool call id means nothing on its own. Fetching some other exchange's trace would
  // draw a chart of a different question's data under this one's sentence.
  stubTrace();

  const view = render(
    <AuthProvider>
      <CitationChip
        citation={chart("pareto", BUILT_INS.pareto as ChartOptions)}
      />
    </AuthProvider>,
  );
  fireEvent.click(
    within(view.container).getAllByRole("button")[0] as HTMLElement,
  );

  const panel = await screen.findByTestId("evidence-panel");
  expect(panel).toHaveAttribute("data-outcome", "unaddressed");
  expect(panel).toHaveTextContent("outside one");
});

test("the request for a chart's data carries the reader's token", async () => {
  // §10.5: every diagnostics endpoint refuses an unauthenticated request, and a chart is
  // read under the identity of whoever is looking at it.
  const fetchMock = stubTrace();

  open(chart("pareto", BUILT_INS.pareto as ChartOptions));
  await drawn();

  const headers = new Headers(
    (fetchMock.mock.calls[0]?.[1] as RequestInit | undefined)?.headers,
  );
  expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
});

test("every built-in type §7.4 names has a reading in this application", () => {
  // Enumerated from the generated contract rather than typed out, so a seventh built-in
  // added to contracts/answer.schema.json fails here instead of quietly going untested.
  const declared = /export type ChartType =([\s\S]+?);/.exec(SOURCE)?.[1] ?? "";
  const types = declared
    .split("|")
    .map((literal) => literal.trim().replace(/["()]/g, ""))
    // `| null` is the optionality every field on the generated `Citation` carries, not a
    // seventh chart type.
    .filter((literal) => literal !== "" && literal !== "null");

  expect(types.length).toBe(7);
  for (const type of types) {
    // The fallback is the one that is not a reading of options: it is a specification.
    if (type === "vega_lite") continue;
    expect(Object.keys(BUILT_INS)).toContain(type);
  }
});
