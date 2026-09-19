/** §7.4's renderer: what a `chart` citation opens onto.
 *
 * *"A chart is a citation, not a decoration beside one."* So it opens the way every other
 * kind opens — through `CitationPanel`, with the same three outcomes — and what it opens
 * onto is the tool call it references, reached through §7.2's trace for this exchange.
 *
 * **Nothing here draws unless there is something measured to draw.** The chain from citation
 * to picture is: the exchange, the trace, the call by id, the call succeeded, the rows at
 * the named path, the rows carrying the named fields, and only then a chart. Each link has a
 * sentence for when it breaks, because the alternative at every one of them is a set of axes
 * with nothing between them — and an empty chart reads as zero, which is §7.4's failure
 * arrived at by omission rather than by invention.
 */
import type { Citation } from "../generated/answer";
import type { ToolCallRecord } from "../generated/trace";
import { fetchTrace, type Trace } from "../api";
import { CitationPanel, NotInTheAnswer } from "../citations/CitationPanel";
import { useResolution } from "../citations/useResolution";
import { useExchange, type Exchange } from "../citations/exchange";
import { dataFree } from "./dataFree";
import { buildFallback, buildSpec } from "./spec";
import { callNamed, rowsAt, type Row } from "./rows";
import { VegaChart } from "./VegaChart";

/** How a chart names itself in a heading and in a failure line. */
export function chartLabel(citation: Citation): string {
  return `${String(citation.chart_type ?? "chart").replace(/_/g, " ")} chart`;
}

export function ChartPanel({ citation }: { citation: Citation }) {
  const exchange = useExchange();

  if (exchange === null) {
    // Rendered outside an answer, so there is no trace to reach and no tool call to name.
    // Said rather than guessed at: fetching some other exchange's trace would draw a chart
    // of a different question's data under this one's sentence.
    return (
      <div
        data-testid="evidence-panel"
        data-outcome="unaddressed"
        className="evidence evidence--error"
      >
        This {chartLabel(citation)} references a tool call, and tool calls
        belong to the exchange that made them — this citation is being shown
        outside one, so there is no trace to draw it from.
      </div>
    );
  }

  return <ChartFromTrace citation={citation} exchange={exchange} />;
}

function ChartFromTrace({
  citation,
  exchange,
}: {
  citation: Citation;
  exchange: Exchange;
}) {
  const resolution = useResolution(
    `trace:${exchange.sessionId}:${String(exchange.seq)}`,
    (token) => fetchTrace(exchange, token),
  );

  return (
    <CitationPanel
      what={`this ${chartLabel(citation)}`}
      resolution={resolution}
    >
      {(trace) => <Drawn citation={citation} trace={trace} />}
    </CitationPanel>
  );
}

/** A refusal that arrived after the trace resolved: the citation opened, and what it opened
 * onto will not make a chart. The same shape `PatternPanel` uses for a cell the report does
 * not hold — it is neither an error nor an empty picture. */
function Refused({ citation, detail }: { citation: Citation; detail: string }) {
  return (
    <NotInTheAnswer what={`This ${chartLabel(citation)}`} detail={detail} />
  );
}

function Drawn({ citation, trace }: { citation: Citation; trace: Trace }) {
  const options = citation.options ?? {};
  const calls = trace.tool_calls ?? [];

  const named = callNamed(calls, citation.source ?? "");
  if ("refused" in named) {
    return <Refused citation={citation} detail={named.refused} />;
  }
  const call = named.call;

  const series = options.series;
  if (!series) {
    return (
      <Refused
        citation={citation}
        detail="it does not say where in the tool result its rows are."
      />
    );
  }

  const found = rowsAt(call.result, series);
  if ("refused" in found) {
    return <Refused citation={citation} detail={found.refused} />;
  }

  const bands = bandRows(citation, calls);
  if (bands !== null && "refused" in bands) {
    return <Refused citation={citation} detail={bands.refused} />;
  }

  const built =
    citation.chart_type === "vega_lite"
      ? fallback(citation, found.rows)
      : buildSpec(
          citation,
          found.rows,
          bands?.rows ?? null,
          citation.window ?? null,
        );

  if ("refused" in built) {
    return <Refused citation={citation} detail={built.refused} />;
  }

  return (
    <>
      <VegaChart
        spec={built.spec}
        description={describe(citation, call, found.rows)}
      />
      <Provenance call={call} rows={found.rows.length} />
    </>
  );
}

/** §7.4's fallback, compiled through the same path as the six.
 *
 * `dataFree` is the only door from the wire into the type `buildFallback` takes, so a
 * specification that carries its own figures is refused here rather than drawn — and the
 * agent refused the same thing when the answer was built. Two refusals at two boundaries,
 * because this is the one chart in the vocabulary whose shape a model wrote.
 */
function fallback(
  citation: Citation,
  rows: readonly Row[],
): { spec: Record<string, unknown> } | { refused: string } {
  const spec = citation.options?.spec;
  if (!spec) {
    return {
      refused: "the free-form fallback carries no specification to render.",
    };
  }
  const checked = dataFree(spec);
  if ("refused" in checked) return checked;
  return {
    spec: buildFallback(citation, checked.spec, rows, citation.window ?? null),
  };
}

/** The rows behind §7.4's event bands, or `null` when the chart asks for none.
 *
 * A second tool call id, resolved exactly like the first: a shaded period drawn from a call
 * that never happened is the same invention as a line drawn from one, over a larger part of
 * the picture.
 */
function bandRows(
  citation: Citation,
  calls: readonly ToolCallRecord[],
): { rows: readonly Row[] } | { refused: string } | null {
  const options = citation.options ?? {};
  if (!options.bands) return null;

  const call = callNamed(calls, options.bands);
  if ("refused" in call) return call;
  if (!options.bands_series) {
    return {
      refused: "its event bands name a tool call but not where its rows are.",
    };
  }
  return rowsAt(call.call.result, options.bands_series);
}

/** What the chart is, for a reader who cannot see it.
 *
 * The tool call is in the sentence rather than only under the picture: a screen reader gets
 * the same provenance a sighted reader does, and §7.4's claim is about provenance.
 */
function describe(
  citation: Citation,
  call: ToolCallRecord,
  rows: readonly Row[],
): string {
  const title = citation.options?.title;
  const window = citation.window;
  return (
    `${title ? `${title}. ` : ""}A ${chartLabel(citation)} of ${String(rows.length)} row(s) ` +
    `from the ${call.name} tool call` +
    `${window ? ` over ${window.label}` : ""}.`
  );
}

/** Which call this chart is a reading of, on the screen under it.
 *
 * §7.2 already puts every tool call with its arguments in the trace; naming the one *this*
 * chart came from is what lets a reader check the picture against it without counting
 * entries. The arguments are shown because `inspection_stats` says nothing about which
 * grouping was asked for, and a chart of the wrong grouping is a chart of real numbers
 * answering a different question.
 */
function Provenance({ call, rows }: { call: ToolCallRecord; rows: number }) {
  const args = Object.entries(call.arguments ?? {})
    .map(([name, value]) => `${name}=${String(value)}`)
    .join(", ");

  return (
    <p className="evidence__note">
      Drawn from the tool call <span className="mono">{call.name}</span>
      {args === "" ? "" : ` (${args})`} — {rows} row(s) of its stored result, as
      it answered during this run. Nothing on this chart is a figure the model
      typed.
    </p>
  );
}
