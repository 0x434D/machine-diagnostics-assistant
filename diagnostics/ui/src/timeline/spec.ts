/** §7.2's stop timeline, as one chart: *"station states as a Gantt across the window,
 * propagation chain drawn on it."*
 *
 * **On it, and not beside it.** §5.4 returns the chain as a structured derivation rather
 * than a verdict, so that §6.5's agent can contradict it — and a reader can only check a
 * contradiction against reasoning they can see. A chain in JSON is a claim; a chain on the
 * axis beside the episodes it was walked over is a claim someone can check by looking.
 *
 * Nothing here supplies a value. Every row below is a record the analysis service returned,
 * and the two fields this file adds to one (`__end`, `__step`) are derived from the record
 * itself: where an episode with no end was drawn to, and which step of the chain a link is.
 */
import type { StopDetail } from "../api";
import type { Row } from "../charts/rows";
import { chrome } from "../charts/palette";
import { stateGanttSpec, titleBlock } from "../charts/spec";

export type Drawn =
  | { readonly spec: Record<string, unknown>; readonly description: string }
  | { readonly refused: string };

/** The last instant this stop's history was read to.
 *
 * `StateEpisode.to_ts` is null while the station was still in that state at the end of the
 * history that was read — *not* "until now", which is a claim about a clock the record has
 * not consulted. A bar has to end somewhere, so it ends here, and `__open` is what stops
 * that drawn end being read as an observed one.
 */
function readEnd(detail: StopDetail): string {
  const ends = detail.timeline
    .map((episode) => episode.to_ts)
    .filter((instant): instant is string => instant !== null);
  return [detail.stop.to_ts, ...ends].reduce((latest, instant) =>
    instant > latest ? instant : latest,
  );
}

/** Each row names the fields it is drawn from and nothing else, rather than copying the
 * whole record: what reaches the chart is then exactly what the chart reads, and a field
 * the service adds later does not silently arrive in a tooltip nobody designed. */
function episodeRows(detail: StopDetail): Row[] {
  const end = readEnd(detail);
  return detail.timeline.map((episode) => ({
    station: episode.station,
    state: episode.state,
    reason: episode.reason,
    from_ts: episode.from_ts,
    __end: episode.to_ts ?? end,
    // Carried into the tooltip, because a bar drawn to the edge of the history read looks
    // exactly like one that was observed to end there.
    __open:
      episode.to_ts === null
        ? "still in this state at the end of the history read"
        : "",
  }));
}

/** §5.4's chain as rows on the same axis: seed first, root last.
 *
 * Each link is an episode the walk passed through, so it lands on the station's own row at
 * the instant that episode began — the same coordinates as the bar beneath it.
 */
function chainRows(detail: StopDetail): Row[] {
  const links = detail.derivation.links;
  const end = readEnd(detail);
  return links.map((link, index) => ({
    station: link.station,
    state: link.state,
    reason: link.reason,
    buffer: link.buffer,
    buffer_condition_since: link.buffer_condition_since,
    from_ts: link.from_ts,
    __end: link.to_ts ?? end,
    __step: index + 1,
    // The buffer is the evidence for the step: S4 was starved *because* B3_4 ran empty.
    // Written onto the chart rather than left to the list below it, so the picture carries
    // the reason and not only the shape.
    __label:
      link.buffer === null
        ? `${String(index + 1)}. root`
        : `${String(index + 1)}. via ${link.buffer}`,
  }));
}

/** The instants a chained episode's named buffer ran empty or full.
 *
 * Drawn as a rule across every station, because that is what the instant is: a fact about
 * the line at a moment, not about one row of the chart. A link whose buffer moment was not
 * found within the lead-in contributes nothing here — the end of the evidence is not the
 * end of the chain, and a rule drawn at a guessed instant would collapse the two.
 */
function bufferRows(detail: StopDetail): Row[] {
  return detail.derivation.links
    .filter(
      (link) => link.buffer !== null && link.buffer_condition_since !== null,
    )
    .map((link) => ({
      at: link.buffer_condition_since,
      buffer: link.buffer,
      station: link.station,
    }));
}

function gapRows(detail: StopDetail): Row[] {
  return detail.coverage.gaps.map((gap) => ({
    from_ts: gap.from_ts,
    to_ts: gap.to_ts,
    reason: gap.reason,
  }));
}

/** The chart, or why there is no chart to draw.
 *
 * A stop whose timeline came back empty is refused by name rather than drawn as an axis
 * with nothing on it: §4.4's whole point is that an empty picture and a quiet line are
 * different facts, and the axes of an empty Gantt are indistinguishable from the axes of a
 * real one.
 */
export function buildTimeline(detail: StopDetail): Drawn {
  const episodes = episodeRows(detail);
  if (episodes.length === 0) {
    return {
      refused:
        "the service returned no state episodes for this stop, so there is nothing to " +
        "draw. That is a statement about the history read, not about the line.",
    };
  }

  const chain = chainRows(detail);
  const buffers = bufferRows(detail);
  const gaps = gapRows(detail);
  const paint = chrome();

  return {
    spec: stateGanttSpec(
      { start: "from_ts", end: "__end", row: "station", state: "state" },
      episodes,
      titleBlock(
        `Stop ${detail.stop.id ?? "(uncitable)"} — station states`,
        `${detail.history_from_ts} → ${detail.stop.to_ts}`,
      ),
      {
        under: gaps.length === 0 ? [] : [gapLayer(gaps, paint.muted)],
        over: [
          ...(chain.length === 0
            ? []
            : [
                chainOutlineLayer(chain, paint.text),
                chainLineLayer(chain, paint.text),
                chainLabelLayer(chain, paint.text),
              ]),
          ...(buffers.length === 0 ? [] : [bufferLayer(buffers, paint.text)]),
        ],
      },
    ),
    description: describe(detail, episodes.length),
  };
}

/** §4.4's ingest gaps, shaded across every station.
 *
 * Under the bars rather than over them: a gap is what the chart could not see, and a band
 * painted over the episodes would hide the ones that *were* recorded either side of it. The
 * dashes are the second channel — a gap is not a pale colour, it is a hatched region.
 */
function gapLayer(
  gaps: readonly Row[],
  colour: string,
): Record<string, unknown> {
  return {
    data: { values: gaps },
    mark: {
      type: "rect",
      fill: colour,
      fillOpacity: 0.14,
      stroke: colour,
      strokeDash: [3, 2],
    },
    encoding: {
      x: { field: "from_ts", type: "temporal", title: null },
      x2: { field: "to_ts" },
      tooltip: [
        { field: "reason", type: "nominal", title: "no data" },
        { field: "from_ts", type: "temporal", title: "from" },
        { field: "to_ts", type: "temporal", title: "to" },
      ],
    },
  };
}

/** The episodes §5.4 actually walked, outlined where they sit.
 *
 * An outline rather than a colour: the five category colours are the one language this
 * screen teaches, and a sixth fill meaning "in the chain" would be a second.
 */
function chainOutlineLayer(
  chain: readonly Row[],
  colour: string,
): Record<string, unknown> {
  return {
    data: { values: chain },
    mark: {
      type: "bar",
      cornerRadius: 2,
      height: { band: 0.7 },
      fillOpacity: 0,
      stroke: colour,
      strokeWidth: 1.5,
    },
    encoding: {
      x: { field: "from_ts", type: "temporal", title: null },
      x2: { field: "__end" },
      y: { field: "station", type: "nominal", title: null },
    },
  };
}

/** The walk itself: seed to root, in the order §5.4 walked it. */
function chainLineLayer(
  chain: readonly Row[],
  colour: string,
): Record<string, unknown> {
  return {
    data: { values: chain },
    mark: {
      type: "line",
      point: { filled: true, size: 44, color: colour },
      color: colour,
      strokeWidth: 1.5,
      strokeDash: [4, 3],
    },
    encoding: {
      x: { field: "from_ts", type: "temporal", title: null },
      y: { field: "station", type: "nominal", title: null },
      order: { field: "__step", type: "quantitative" },
      tooltip: [
        { field: "__step", type: "quantitative", title: "step" },
        { field: "station", type: "nominal" },
        { field: "state", type: "nominal" },
        { field: "reason", type: "nominal" },
        { field: "buffer", type: "nominal" },
        {
          field: "buffer_condition_since",
          type: "temporal",
          title: "buffer in that condition since",
        },
      ],
    },
  };
}

/** Which step each point is, and the buffer it followed — the chain read without a tooltip. */
function chainLabelLayer(
  chain: readonly Row[],
  colour: string,
): Record<string, unknown> {
  return {
    data: { values: chain },
    mark: {
      type: "text",
      align: "left",
      baseline: "bottom",
      dx: 4,
      dy: -6,
      fontSize: 10,
      color: colour,
    },
    encoding: {
      x: { field: "from_ts", type: "temporal", title: null },
      y: { field: "station", type: "nominal", title: null },
      text: { field: "__label", type: "nominal" },
    },
  };
}

/** The instant a chained link's buffer ran empty or full, as a rule across the chart.
 *
 * This is what makes a step checkable by eye: S4 starved at 02:14:32 because B3_4 was empty
 * from 02:14:02, and both instants are on the same ruler.
 */
function bufferLayer(
  buffers: readonly Row[],
  colour: string,
): Record<string, unknown> {
  return {
    data: { values: buffers },
    mark: { type: "rule", stroke: colour, strokeDash: [1, 3], opacity: 0.8 },
    encoding: {
      x: { field: "at", type: "temporal", title: null },
      tooltip: [
        { field: "buffer", type: "nominal", title: "empty or full since" },
        { field: "at", type: "temporal" },
      ],
    },
  };
}

/** What the chart shows, for a reader who cannot see it.
 *
 * The chain is in the sentence rather than only in the picture: §7.2's derivation has to be
 * readable, and a screen reader that got "a chart" would be told a picture exists and
 * nothing about what it derives.
 */
function describe(detail: StopDetail, episodes: number): string {
  const stations = new Set(detail.timeline.map((episode) => episode.station));
  const links = detail.derivation.links;
  const walk =
    links.length === 0
      ? "No propagation chain was derived for this stop."
      : `The propagation chain runs ${links
          .map((link) => `${link.station} ${link.state}`)
          .join(" ← ")}, drawn on the same axis.`;
  const gaps =
    detail.coverage.gaps.length === 0
      ? ""
      : ` ${String(detail.coverage.gaps.length)} ingest gap(s) are shaded: inside them the line was not observed.`;

  return (
    `Station states for stop ${detail.stop.id ?? "(uncitable)"}: ` +
    `${String(episodes)} episode(s) across ${String(stations.size)} station(s), ` +
    `${detail.history_from_ts} to ${detail.stop.to_ts}. ${walk}${gaps}`
  );
}
