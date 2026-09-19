/** §7.4's vocabulary, as Vega-Lite specifications built from rows that came out of a tool
 * result.
 *
 * **One rendering path.** §15 left the charting library open and named Recharts, visx and
 * ECharts; none was taken, and the six built-in types compile to Vega-Lite like the
 * free-form fallback does. That is one dependency instead of two, and — the reason that
 * matters more — it stops the fallback being the least-exercised code in the frontend. §7.4
 * says so in as many words: *"the fallback path is the least-exercised code in the frontend
 * precisely because it fires rarely"*. Sharing the path the built-ins use daily is a
 * stronger guarantee than the tests it also has.
 *
 * **Nothing here supplies a value.** Every function takes rows and returns a specification
 * that names their fields; the arithmetic that a Pareto needs is a Vega-Lite `transform`
 * over those same rows rather than a running total computed in TypeScript, so what is drawn
 * can be traced back to what was measured without reading this file.
 *
 * The one thing computed here is the **category** of a PackML state (§15's five), because
 * that mapping is the cause-and-consequence distinction §5.4 rests on and is not something a
 * model may assign.
 */
import type { Citation, CitationWindow } from "../generated/answer";
import type { DataFree } from "./dataFree";
import {
  categoryGlyph,
  categoryLabel,
  chrome,
  seriesRange,
  stateScale,
} from "./palette";
import { fieldsPresent, type Row } from "./rows";

export type Built =
  { readonly spec: Record<string, unknown> } | { readonly refused: string };

const VEGA_LITE = "https://vega.github.io/schema/vega-lite/v6.json";

/** The chart's own look, in the application's tokens.
 *
 * `background: null` so the panel's surface shows through in both schemes rather than the
 * chart painting a white rectangle onto a dark page.
 */
function config(): Record<string, unknown> {
  const paint = chrome();
  return {
    background: null,
    config: {
      font: paint.font,
      background: null,
      view: { stroke: null },
      axis: {
        labelColor: paint.muted,
        titleColor: paint.text,
        domainColor: paint.grid,
        gridColor: paint.grid,
        tickColor: paint.grid,
        labelFontSize: 11,
        titleFontSize: 11,
      },
      legend: {
        labelColor: paint.muted,
        titleColor: paint.text,
        labelFontSize: 11,
        titleFontSize: 11,
      },
      title: { color: paint.text, subtitleColor: paint.muted, anchor: "start" },
    },
  };
}

/** A chart's title block: what it is, and the interval it is a reading of. */
export function titleBlock(
  text: string,
  subtitle: string,
): Record<string, unknown> {
  return { title: { text, subtitle } };
}

/** The heading over every chart drawn from a citation.
 *
 * The window is the one the pipeline stamped (§7.3) and never one the screen chose — a
 * chart labelled with a different interval is §7.4's failure with the values left true and
 * the ruler made up, which is no easier for a reader to catch.
 */
function heading(
  citation: Citation,
  fallback: string,
  window: CitationWindow | null,
): Record<string, unknown> {
  return titleBlock(
    citation.options?.title ?? fallback,
    window === null ? "" : window.label,
  );
}

/** Which of the option names a type reads, checked against the rows before anything draws.
 *
 * A field the rows do not carry renders in Vega-Lite as a complete set of axes with no
 * marks on them — and that picture is indistinguishable from a line that made nothing.
 */
function reading(
  rows: readonly Row[],
  ...fields: readonly (string | null | undefined)[]
): string | null {
  const named = fields.filter((field): field is string => Boolean(field));
  const missing = fields.length - named.length;
  if (missing > 0) {
    return "this chart does not say which fields of the tool result it reads.";
  }
  return fieldsPresent(rows, named);
}

export function buildSpec(
  citation: Citation,
  rows: readonly Row[],
  bands: readonly Row[] | null,
  window: CitationWindow | null,
): Built {
  switch (citation.chart_type) {
    case "timeseries":
      return timeseries(citation, rows, bands, window);
    case "state_gantt":
      return stateGantt(citation, rows, window);
    case "pareto":
      return pareto(citation, rows, window);
    case "stacked_bar":
      return stackedBar(citation, rows, window);
    case "rate_over_time":
      return rateOverTime(citation, rows, window);
    case "summary_tiles":
      return summaryTiles(citation, rows, window);
    default:
      // `vega_lite` is built by `buildFallback` below, which takes a `DataFree` rather than
      // rows; and a `chart_type` that is neither is a contract this build does not know.
      return {
        refused: `${String(citation.chart_type)} is not a chart type this application renders.`,
      };
  }
}

/** §7.4: *"a process value with stop and alarm periods shaded"*.
 *
 * The y scale deliberately does **not** include zero. A joining force that drifts from 904
 * to 912 N is the whole finding, and an axis anchored at zero draws it as a flat line —
 * which is the same wrong answer as an invented chart, arrived at by a default.
 */
function timeseries(
  citation: Citation,
  rows: readonly Row[],
  bands: readonly Row[] | null,
  window: CitationWindow | null,
): Built {
  const options = citation.options ?? {};
  const problem = reading(rows, options.x, options.y);
  if (problem !== null) return { refused: problem };

  const paint = chrome();
  const layers: Record<string, unknown>[] = [];

  if (bands !== null) {
    const bandProblem = reading(bands, options.bands_x, options.bands_end);
    if (bandProblem !== null) return { refused: bandProblem };
    layers.push({
      data: { values: bands },
      // Shaded and not outlined: the band is context behind the series, and an outline
      // would read as a second measurement drawn on the same axes.
      mark: { type: "rect", opacity: 0.18, fill: paint.danger },
      encoding: {
        x: { field: options.bands_x, type: "temporal" },
        x2: { field: options.bands_end },
      },
    });
  }

  layers.push({
    data: { values: rows },
    mark: { type: "line", point: true, color: paint.accent, strokeWidth: 1.5 },
    encoding: {
      x: { field: options.x, type: "temporal", title: options.x },
      y: {
        field: options.y,
        type: "quantitative",
        title: options.y,
        scale: { zero: false },
      },
      tooltip: [
        { field: options.x, type: "temporal" },
        { field: options.y, type: "quantitative" },
      ],
    },
  });

  return {
    spec: {
      $schema: VEGA_LITE,
      ...heading(citation, `${String(options.y)} over time`, window),
      ...config(),
      layer: layers,
      resolve: { scale: { x: "shared" } },
    },
  };
}

/** Which fields of its rows a Gantt reads: when an episode began and ended, which row of
 * the chart it belongs on, and which PackML state it is. Names, never values. */
export interface GanttFields {
  readonly start: string;
  readonly end: string;
  readonly row: string;
  readonly state: string;
}

/** Anything else drawn on the same axis, under the episodes or over them.
 *
 * §7.2's stop timeline puts §5.4's chain and §4.4's ingest gaps onto this chart, and it is
 * the *same* chart — one station per row, one bar per episode, coloured by category. A
 * second implementation of it would be a second colour language the day either changed.
 */
export interface GanttOverlays {
  readonly under?: readonly Record<string, unknown>[];
  readonly over?: readonly Record<string, unknown>[];
}

/** How far apart the two rows of a stacked Gantt sit, in pixels. Small on purpose: the
 * second row is evidence for the first and the eye has to travel between them. */
const ROW_GAP = 6;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The same layer with no time axis of its own.
 *
 * A Gantt that is the upper row of a stacked chart shares the lower row's x scale, so the
 * two would otherwise draw the same ruler six pixels apart — and an axis between the rows
 * is exactly the distance that makes reading one against the other guesswork.
 */
function withoutTimeAxis(
  layer: Record<string, unknown>,
): Record<string, unknown> {
  const encoding = layer.encoding;
  if (!isObject(encoding)) return layer;
  const x = encoding.x;
  if (!isObject(x)) return layer;
  return { ...layer, encoding: { ...encoding, x: { ...x, axis: null } } };
}

/** §7.4's state Gantt, and §7.2's stop timeline: station states across an interval, in
 * §15's five categories.
 *
 * Coloured by category rather than by state, for the reason `design/stateCategory.ts`
 * gives at length: fifteen PackML states in fifteen colours hides the one distinction the
 * chart exists to show. The glyph rides on each bar and again in the legend, so the
 * distinction survives with the colour gone.
 *
 * Every layer carries its own encoding rather than inheriting a top-level one, so an
 * overlay can position itself differently — a gap is a band across every station and not a
 * bar on one of them, and an inherited `y` would have forced it onto a row it is not about.
 *
 * `beneath` is a second view under the episodes, on their time axis. An overlay cannot do
 * that job: a series with a scale of its own — §7.2 draws buffer levels there, which are
 * carriers and not states — would have to borrow the nominal station axis, and would land
 * on a row it is not about. A second row has its own y and still shares the one ruler, so
 * the two can be read against each other. With no second row this returns what it always
 * did, which is what every chart citation renders.
 */
export function stateGanttSpec(
  fields: GanttFields,
  rows: readonly Row[],
  title: Record<string, unknown>,
  overlays: GanttOverlays = {},
  beneath: Record<string, unknown> | null = null,
): Record<string, unknown> {
  const values = rows.map((row) => ({
    ...row,
    // Derived, not supplied: `categoryFor` is §3.3's table and the same one `StateBadge`
    // reads, so a bar and a badge for one state cannot disagree.
    __category: categoryLabel(row[fields.state]),
    __glyph: categoryGlyph(row[fields.state]),
  }));

  const paint = chrome();
  const position = {
    y: { field: fields.row, type: "nominal", title: null },
    x: { field: fields.start, type: "temporal", title: null },
  };

  const layers: Record<string, unknown>[] = [
    ...(overlays.under ?? []),
    {
      data: { values },
      mark: { type: "bar", cornerRadius: 2, height: { band: 0.7 } },
      encoding: {
        ...position,
        x2: { field: fields.end },
        color: {
          field: "__category",
          type: "nominal",
          scale: stateScale(),
          // The five in the order `CATEGORIES` declares them, so the legend reads as
          // the diagnostic sequence rather than alphabetically.
          sort: stateScale().domain,
          legend: { title: "category" },
        },
        tooltip: [
          { field: fields.row, type: "nominal" },
          { field: fields.state, type: "nominal", title: "state" },
          { field: "__category", type: "nominal", title: "category" },
          { field: fields.start, type: "temporal", title: "from" },
          { field: fields.end, type: "temporal", title: "to" },
        ],
      },
    },
    {
      // The second channel, on the mark itself. Without it a reader has to go to the
      // legend and match a colour, which is the colour being the only channel.
      data: { values },
      mark: {
        type: "text",
        align: "left",
        baseline: "middle",
        dx: 3,
        fontSize: 11,
        color: paint.text,
      },
      encoding: {
        ...position,
        text: { field: "__glyph", type: "nominal" },
      },
    },
    ...(overlays.over ?? []),
  ];

  const head = { $schema: VEGA_LITE, ...title, ...config() };
  if (beneath === null) return { ...head, layer: layers };

  return {
    ...head,
    vconcat: [{ layer: layers.map(withoutTimeAxis) }, beneath],
    // The one ruler both rows are read off. Without it Vega-Lite gives each row a time
    // scale of its own, and two rows with different domains drawn one above the other is
    // the picture that makes a wrong reading look like a checked one.
    resolve: { scale: { x: "shared" } },
    spacing: ROW_GAP,
    // Plotting areas aligned rather than whole views: the second row carries the axis and
    // the legends differ in width, and without this the two time scales would start at
    // different pixels.
    bounds: "flush",
  };
}

function stateGantt(
  citation: Citation,
  rows: readonly Row[],
  window: CitationWindow | null,
): Built {
  const options = citation.options ?? {};
  const problem = reading(
    rows,
    options.x,
    options.end,
    options.y,
    options.colour,
  );
  if (problem !== null) return { refused: problem };

  return {
    spec: stateGanttSpec(
      {
        start: String(options.x),
        end: String(options.end),
        row: String(options.y),
        state: String(options.colour),
      },
      rows,
      heading(citation, "station states across the window", window),
    ),
  };
}

/** §7.4: defect classes by frequency — bars descending, with the cumulative share over them.
 *
 * The cumulative line is what makes this a Pareto rather than a sorted bar chart, and it is
 * a Vega-Lite `window` transform over the referenced rows rather than a running total added
 * in TypeScript: the arithmetic stays visible in the specification, and nothing in the
 * drawing came from anywhere but the tool result.
 */
function pareto(
  citation: Citation,
  rows: readonly Row[],
  window: CitationWindow | null,
): Built {
  const options = citation.options ?? {};
  const problem = reading(rows, options.x, options.y);
  if (problem !== null) return { refused: problem };

  const paint = chrome();
  return {
    spec: {
      $schema: VEGA_LITE,
      ...heading(citation, `${String(options.x)} by frequency`, window),
      ...config(),
      data: { values: rows },
      transform: [
        {
          window: [{ op: "sum", field: options.y, as: "__cumulative" }],
          sort: [{ field: options.y, order: "descending" }],
          frame: [null, 0],
        },
        { joinaggregate: [{ op: "sum", field: options.y, as: "__total" }] },
        { calculate: "datum.__cumulative / datum.__total", as: "__share" },
      ],
      encoding: {
        x: {
          field: options.x,
          type: "nominal",
          title: options.x,
          sort: { field: options.y, op: "sum", order: "descending" },
        },
      },
      layer: [
        {
          mark: { type: "bar", color: paint.accent },
          encoding: {
            y: { field: options.y, type: "quantitative", title: options.y },
            tooltip: [
              { field: options.x, type: "nominal" },
              { field: options.y, type: "quantitative" },
            ],
          },
        },
        {
          mark: {
            type: "line",
            point: true,
            color: paint.danger,
            strokeWidth: 1.5,
          },
          encoding: {
            y: {
              field: "__share",
              type: "quantitative",
              title: "cumulative share",
              axis: { format: ".0%" },
              scale: { domain: [0, 1] },
            },
          },
        },
      ],
      resolve: { scale: { y: "independent" } },
    },
  };
}

/** §7.4: defects by carrier or by lane — one bar per value of the dimension, split by class.
 *
 * Coloured from the series palette and **not** from the five categories: red there means
 * "this station is the problem", and spending it on defect class 3 would teach the reader
 * the wrong language (see `tokens.css`). Each segment carries its own label, so the split
 * is readable without the colour.
 */
function stackedBar(
  citation: Citation,
  rows: readonly Row[],
  window: CitationWindow | null,
): Built {
  const options = citation.options ?? {};
  const problem = reading(rows, options.x, options.y, options.colour);
  if (problem !== null) return { refused: problem };

  const paint = chrome();
  return {
    spec: {
      $schema: VEGA_LITE,
      ...heading(
        citation,
        `${String(options.y)} by ${String(options.x)}`,
        window,
      ),
      ...config(),
      data: { values: rows },
      encoding: {
        x: { field: options.x, type: "nominal", title: options.x },
        y: {
          field: options.y,
          type: "quantitative",
          stack: "zero",
          title: options.y,
        },
        color: {
          field: options.colour,
          type: "nominal",
          scale: { range: seriesRange() },
          legend: { title: options.colour },
        },
        order: { field: options.colour, type: "nominal" },
      },
      layer: [
        { mark: { type: "bar" } },
        {
          // The second channel: the value of the dimension written into its own segment.
          mark: {
            type: "text",
            baseline: "middle",
            fontSize: 10,
            color: paint.text,
          },
          encoding: { text: { field: options.colour, type: "nominal" } },
        },
      ],
    },
  };
}

/** §7.4: scrap rate, micro-stop rate. A rate over time, and not a process value.
 *
 * The difference from `timeseries` is the y scale, and it is the whole reason the vocabulary
 * has both: a rate is anchored at zero because the distance from zero is what it means,
 * while a process value is not because its variation is. Reading a rate off an axis that
 * starts at 4 % is how "scrap doubled" reads as a small step.
 */
function rateOverTime(
  citation: Citation,
  rows: readonly Row[],
  window: CitationWindow | null,
): Built {
  const options = citation.options ?? {};
  const problem = reading(rows, options.x, options.y);
  if (problem !== null) return { refused: problem };

  const field = String(options.y);
  // A rate the service reports as a fraction is drawn as a percentage, because 0.043 on an
  // axis is a figure a reader converts in their head and 4.3 % is one they do not. Decided
  // from the values themselves rather than from the field's name, which is not a contract.
  const fraction = rows.every((row) => {
    const value = row[field];
    return typeof value === "number" && value >= 0 && value <= 1;
  });

  const paint = chrome();
  return {
    spec: {
      $schema: VEGA_LITE,
      ...heading(citation, `${field} over time`, window),
      ...config(),
      data: { values: rows },
      mark: {
        type: "line",
        point: true,
        color: paint.accent,
        strokeWidth: 1.5,
      },
      encoding: {
        x: { field: options.x, type: "temporal", title: null },
        y: {
          field: options.y,
          type: "quantitative",
          title: options.y,
          scale: { zero: true },
          ...(fraction ? { axis: { format: ".1%" } } : {}),
        },
        tooltip: [
          { field: options.x, type: "temporal" },
          {
            field: options.y,
            type: "quantitative",
            ...(fraction ? { format: ".1%" } : {}),
          },
        ],
      },
    },
  };
}

const TILE_HEIGHT = 84;

/** §7.4: counts, rates, availability figures.
 *
 * Through Vega-Lite like the other five rather than as a grid of `<div>`s. One rendering
 * path was the ruling, and the cost of honouring it here is a layered text mark; the gain is
 * that a reader who exports a chart gets the same object whichever type it was.
 */
function summaryTiles(
  citation: Citation,
  rows: readonly Row[],
  window: CitationWindow | null,
): Built {
  const options = citation.options ?? {};
  const problem = reading(rows, options.x, options.y);
  if (problem !== null) return { refused: problem };

  const paint = chrome();
  return {
    spec: {
      $schema: VEGA_LITE,
      ...heading(citation, "summary", window),
      ...config(),
      data: { values: rows },
      height: TILE_HEIGHT,
      width: { step: 132 },
      encoding: {
        x: {
          field: options.x,
          type: "nominal",
          axis: null,
          scale: { paddingInner: 0.18, paddingOuter: 0.05 },
        },
      },
      layer: [
        {
          mark: {
            type: "rect",
            cornerRadius: 8,
            fill: paint.surface,
            stroke: paint.grid,
          },
          encoding: { y: { value: 0 }, y2: { value: TILE_HEIGHT } },
        },
        {
          mark: {
            type: "text",
            baseline: "middle",
            fontSize: 26,
            fontWeight: 600,
            dy: -8,
            color: paint.text,
          },
          encoding: {
            y: { value: TILE_HEIGHT / 2 },
            text: { field: options.y, type: "quantitative" },
          },
        },
        {
          // The label, which is what makes a figure a figure about something.
          mark: {
            type: "text",
            baseline: "middle",
            fontSize: 11,
            dy: 20,
            color: paint.muted,
          },
          encoding: {
            y: { value: TILE_HEIGHT / 2 },
            text: { field: options.x, type: "nominal" },
          },
        },
      ],
    },
  };
}

/** §7.4's fallback: *"anything that fits none of them falls through to a declarative
 * free-form specification (Vega-Lite), which is data rather than code and therefore safe."*
 *
 * Safe because of the type it takes. `DataFree` is the only shape that reaches here, its
 * one door is `dataFree`, and that refuses `data` and `datasets` at every depth — so the
 * rows bound below are the verified tool result and there is nothing else for the chart to
 * be drawn from. What the model wrote is the marks and the encoding; the values are ours.
 *
 * The heading and the configuration are applied over the model's specification rather than
 * under it, so a fallback chart is titled with the same window every built-in is and cannot
 * label itself with an interval of its own.
 */
export function buildFallback(
  citation: Citation,
  spec: DataFree,
  rows: readonly Row[],
  window: CitationWindow | null,
): Record<string, unknown> {
  return {
    $schema: VEGA_LITE,
    ...spec,
    ...heading(citation, "chart", window),
    ...config(),
    data: { values: rows },
  };
}
