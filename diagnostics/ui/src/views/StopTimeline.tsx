/** §7.2's stop timeline: *"station states as a Gantt across the window, propagation chain
 * drawn on it."*
 *
 * M3 computes §5.4's chain and returns it as a derivation rather than a verdict, precisely
 * so that §6.5's agent can contradict it. Nothing has ever drawn it. A chain in JSON is a
 * claim; a chain on an axis beside the episodes it was walked over is a claim a reader can
 * check by looking, which is the whole reason the service returns the derivation and not
 * just the answer.
 *
 * **The window is in the address.** A stop citation opens this view at its own stop, a
 * colleague can be sent the link, and the interval a question resolved to survives a reload
 * — none of which is true of a window held in a component's state. What the screen never
 * does is *invent* one: `/time/resolve` resolves a phrase against the shift calendar (§5.3)
 * and the two instant boxes take instants. A browser computing "last night" for itself would
 * be the guess §5.3 exists to prevent, one layer further out.
 */
import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";

import {
  describeFailure,
  fetchStop,
  fetchStops,
  resolveTime,
  type StopDetail,
  type StopList,
} from "../api";
import { useAuth } from "../AuthContext";
import { VegaChart } from "../charts/VegaChart";
import { CitationPanel } from "../citations/CitationPanel";
import { Chain } from "../citations/panels";
import { useResolution } from "../citations/useResolution";
import { CoverageNote, coverageVerdict, type CoverageKind } from "../coverage";
import { StateBadge } from "../design/StateBadge";
import { buildTimeline } from "../timeline/spec";

export function StopTimeline() {
  const [params, setParams] = useSearchParams();
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const stop = params.get("stop");

  /** A window change drops the selected stop: the stop that was on the screen belongs to
   * the interval that was on the screen, and leaving its chart under a different window's
   * list would put two intervals on one page with nothing saying which is which. */
  function chooseWindow(nextFrom: string, nextTo: string): void {
    setParams(new URLSearchParams({ from: nextFrom, to: nextTo }));
  }

  function chooseStop(identifier: string): void {
    const next = new URLSearchParams(params);
    next.set("stop", identifier);
    setParams(next);
  }

  return (
    <section className="timeline">
      <h2>Stop timeline</h2>
      <p className="timeline__lede">
        Every station&apos;s state episodes across the window, and §5.4&apos;s
        propagation chain drawn on the same axis — the consequence episodes and
        the cause they were derived from, so the derivation is visible rather
        than asserted. Colour is by category, never by PackML state; the key is
        under every view.
      </p>

      <WindowPicker from={from} to={to} onChoose={chooseWindow} />

      {from !== "" && to !== "" ? (
        <StopsInWindow
          from={from}
          to={to}
          selected={stop}
          onChoose={chooseStop}
        />
      ) : stop === null ? (
        // No window and no stop: nothing has been asked for, so nothing is read. A stop
        // opened on its own (from a citation) needs no window — it carries its own.
        <p className="timeline__empty">
          No window yet. Resolve a phrase against the shift calendar, or give
          two UTC instants. Nothing is fetched until one of the two says which
          interval to read — a window this screen picked for you would be a
          reading of an interval nobody asked about.
        </p>
      ) : null}

      {stop === null ? null : <StopFigure identifier={stop} />}
    </section>
  );
}

/** The window, changed the two ways that involve no guessing: a phrase the shift calendar
 * understands, and a pair of instants. */
function WindowPicker({
  from,
  to,
  onChoose,
}: {
  from: string;
  to: string;
  onChoose: (from: string, to: string) => void;
}) {
  const { token } = useAuth();
  const [draft, setDraft] = useState({ from, to });
  const [refusal, setRefusal] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  // The address is the window, so a link opened, a stop citation followed or a back button
  // pressed has to arrive in the boxes as well as in the chart.
  useEffect(() => {
    setDraft({ from, to });
  }, [from, to]);

  function submitInstants(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const unreadable = [draft.from, draft.to].filter((instant) =>
      Number.isNaN(Date.parse(instant)),
    );
    if (unreadable.length > 0) {
      // Refused rather than guessed at. A date the browser parses loosely — "12/09/2026" is
      // two different days on two continents — would silently answer a different question.
      setRefusal(
        `${unreadable.join(" and ")} is not an instant. Give a UTC instant, as the service writes them: 2026-09-12T01:00:00Z.`,
      );
      return;
    }
    setRefusal(null);
    onChoose(draft.from, draft.to);
  }

  function submitPhrase(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const expression = String(form.get("expression") ?? "").trim();
    if (expression === "") return;

    setResolving(true);
    resolveTime(expression, token)
      .then((resolution) => {
        setRefusal(null);
        onChoose(resolution.window.from_ts, resolution.window.to_ts);
      })
      .catch((reason: unknown) => {
        // Not swallowed: the service's 422 carries every expression that *would* have
        // worked, which is the half of the refusal that lets a reader try again.
        setRefusal(describeFailure(reason));
      })
      .finally(() => {
        setResolving(false);
      });
  }

  return (
    <div className="timeline__window">
      <form className="timeline__phrase" onSubmit={submitPhrase}>
        <label htmlFor="expression">
          A phrase the shift calendar understands
        </label>
        <input
          id="expression"
          name="expression"
          defaultValue=""
          placeholder="last night"
        />
        <button type="submit" disabled={resolving}>
          {resolving ? "Resolving…" : "Resolve"}
        </button>
      </form>

      <form className="timeline__instants" onSubmit={submitInstants}>
        <label htmlFor="from">From (UTC)</label>
        <input
          id="from"
          name="from"
          className="mono"
          value={draft.from}
          onChange={(event) => {
            setDraft({ ...draft, from: event.target.value });
          }}
        />
        <label htmlFor="to">To (UTC)</label>
        <input
          id="to"
          name="to"
          className="mono"
          value={draft.to}
          onChange={(event) => {
            setDraft({ ...draft, to: event.target.value });
          }}
        />
        <button type="submit">Show stops</button>
      </form>

      {refusal === null ? null : <p className="evidence__missing">{refusal}</p>}
    </div>
  );
}

/** Which stops the window holds — and, when it holds none, which of §4.4's three situations
 * that is. */
function StopsInWindow({
  from,
  to,
  selected,
  onChoose,
}: {
  from: string;
  to: string;
  selected: string | null;
  onChoose: (identifier: string) => void;
}) {
  const resolution = useResolution(`stops:${from}:${to}`, (token) =>
    fetchStops(from, to, token),
  );

  return (
    <CitationPanel
      what={`the stops between ${from} and ${to}`}
      resolution={resolution}
    >
      {(list) => (
        <StopChoices list={list} selected={selected} onChoose={onChoose} />
      )}
    </CitationPanel>
  );
}

function StopChoices({
  list,
  selected,
  onChoose,
}: {
  list: StopList;
  selected: string | null;
  onChoose: (identifier: string) => void;
}) {
  const verdict = coverageVerdict(list.coverage);

  return (
    <>
      <CoverageNote what="this window" coverage={list.coverage} />
      {list.stops.length === 0 ? (
        <p className="evidence__missing" data-testid="no-stops">
          No stop in this window. {whyNoStops(verdict.kind)}
        </p>
      ) : (
        <ul className="timeline__stops">
          {list.stops.map((stop) => (
            <li key={`${stop.from_ts}:${stop.to_ts}`}>
              {stop.id === null ? (
                // §5.3: the window opens before the history does, so this stop's start is
                // the caller's own boundary and there is no instant `/stops/{id}` could
                // verify. A button minting an id would open onto a 404.
                <span className="timeline__uncitable">
                  <span className="mono">{stop.from_ts}</span> —{" "}
                  {stop.duration_seconds.toFixed(1)} s, and no id: this stop
                  began before the history did, so there is nothing to open.
                </span>
              ) : (
                <button
                  type="button"
                  className="citation__chip"
                  aria-pressed={stop.id === selected}
                  onClick={() => {
                    onChoose(stop.id ?? "");
                  }}
                >
                  <span className="mono">{stop.from_ts}</span> ·{" "}
                  {stop.duration_seconds.toFixed(1)} s ·{" "}
                  {stop.category ?? "not derived"}
                  {stop.open_at_window_end ? " · still open" : ""}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="evidence__note">
        {list.micro_stops} micro-stop(s) beneath the{" "}
        {list.micro_stop_threshold_seconds} s threshold — counted, not listed,
        and a rising count is its own diagnostic signal (§5.4).
        {list.truncated
          ? " The list was cut short by the service, which is not the same as a window with fewer stops in it."
          : ""}
      </p>
    </>
  );
}

/** Why a window holds no stop — which is a different fact in each of §4.4's situations.
 *
 * The one thing it must never be is the same blank region four times over. "The line ran
 * without stopping" and "the gateway was down" are opposite readings of an identical empty
 * list, and a screen that renders them alike hands a reader the wrong one half the time.
 */
function whyNoStops(kind: CoverageKind): string {
  switch (kind) {
    case "blackout":
      return "None could be reported: there is no data here to find one in, so this window says nothing about the line at all.";
    case "partial":
      return "None over the covered part of it — inside the gaps above a stop would not have been recorded, so this is not an answer for the whole window.";
    case "quiet":
      return "And nothing else was recorded either: the gateway was up and the line silent, which is not the same as a line that ran without stopping.";
    case "covered":
      return "The data over this window is complete, so this is a line that ran without stopping.";
  }
}

/** Where the data is not, written out beside the chart that shades it. */
/** One stop: the Gantt, the chain on it, and the same chain written out beneath. */
function StopFigure({ identifier }: { identifier: string }) {
  const resolution = useResolution(`timeline:${identifier}`, (token) =>
    fetchStop(identifier, token),
  );

  return (
    <CitationPanel what={`stop ${identifier}`} resolution={resolution}>
      {(detail) => <Figure detail={detail} />}
    </CitationPanel>
  );
}

function Figure({ detail }: { detail: StopDetail }) {
  const drawn = buildTimeline(detail);

  return (
    <>
      <p className="evidence__window">
        Stop <span className="mono">{detail.stop.id ?? "(uncitable)"}</span>,
        read from <span className="mono">{detail.history_from_ts}</span> — the
        run-up is on the chart, not only the interruption.
      </p>
      <CoverageNote what="the stop itself" coverage={detail.coverage} />

      {"refused" in drawn ? (
        <p className="evidence__missing">{drawn.refused}</p>
      ) : (
        <VegaChart spec={drawn.spec} description={drawn.description} />
      )}

      <Chain derivation={detail.derivation} />

      <p className="evidence__note">
        Alarms over this stop:{" "}
        {detail.alarms.length === 0
          ? "none"
          : detail.alarms
              .map(
                (alarm) => `${alarm.station} ${alarm.code} (${alarm.status})`,
              )
              .join(" · ")}
        . An annotation and nothing more — §3.3 warns that &ldquo;the first
        station to raise an alarm&rdquo; is circular, so the chain above was
        computed without them.
      </p>

      <Episodes detail={detail} />
    </>
  );
}

/** The same episodes as text.
 *
 * Not a duplicate of the chart but the other half of ISA-101's rule: colour is never the
 * only channel, and a Gantt read aloud is a picture with a caption. Every state here goes
 * through `StateBadge`, so the category a bar is painted and the category a row is labelled
 * come from one table and cannot disagree.
 */
function Episodes({ detail }: { detail: StopDetail }) {
  return (
    <details className="timeline__episodes">
      <summary>Every episode as text ({detail.timeline.length} row(s))</summary>
      <div className="scroller">
        <table className="episodes">
          <thead>
            <tr>
              <th scope="col">Station</th>
              <th scope="col">State</th>
              <th scope="col">From</th>
              <th scope="col">To</th>
            </tr>
          </thead>
          <tbody>
            {detail.timeline.map((episode) => (
              <tr key={`${episode.station}:${episode.from_ts}`}>
                <td className="mono">{episode.station}</td>
                <td>
                  <StateBadge state={episode.state} reason={episode.reason} />
                </td>
                <td className="mono">{episode.from_ts}</td>
                <td className="mono">
                  {episode.to_ts ??
                    "still in this state at the end of the history read"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
