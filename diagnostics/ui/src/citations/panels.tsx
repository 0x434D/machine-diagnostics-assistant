/** What each kind of citation opens onto (§7.3).
 *
 * Nine kinds, nine panels, one file — because §7.3's claim is about the *set*: "each kind
 * has a renderer; adding a citation type means adding a renderer, nothing more". Reading
 * them side by side is what makes a kind that resolves onto nothing obvious.
 *
 * Each panel shows the record the endpoint returned rather than a summary of it. Where the
 * service says a thing is unknown, absent or incomplete, the panel says which of the three
 * — §4.4 and §6.5 both rest on those not collapsing into a blank.
 */
import { Fragment } from "react";

import {
  fetchAlarm,
  fetchComponentAssembly,
  fetchKnowledgeDocument,
  fetchLotParts,
  fetchPatterns,
  fetchSignalTrend,
  fetchStop,
  type LotParts,
  type StopDetail,
} from "../api";
import type { CitationWindow } from "../generated/answer";
import { StateBadge } from "../design/StateBadge";
import { CitationPanel, NotInTheAnswer } from "./CitationPanel";
import { PartLinks } from "./PartLinks";
import { useResolution } from "./useResolution";

// --- the id-shaped kinds ----------------------------------------------------------------

/** §5.3's `/stops/{id}`: the stop, its coverage, the alarms beside it and §5.4's chain. */
export function StopPanel({ identifier }: { identifier: string }) {
  const resolution = useResolution(`stop:${identifier}`, (token) =>
    fetchStop(identifier, token),
  );

  return (
    <CitationPanel what={`stop ${identifier}`} resolution={resolution}>
      {(detail) => (
        <>
          <dl>
            <dt>Stop</dt>
            {/* The service's canonical id, which is not always the one that was cited: a
                stop already running when a window opened is reported at that window's edge
                and resolves back to where it actually began. */}
            <dd className="mono">{detail.stop.id ?? identifier}</dd>
            <dt>From</dt>
            <dd className="mono">{detail.stop.from_ts}</dd>
            <dt>To</dt>
            <dd className="mono">
              {detail.stop.to_ts}
              {detail.stop.open_at_window_end
                ? " — still open, so this end is a reading of the clock and not a part leaving"
                : ""}
            </dd>
            <dt>Duration</dt>
            <dd>{detail.stop.duration_seconds.toFixed(1)} s</dd>
            <dt>Category</dt>
            <dd>{detail.stop.category ?? "not derived"}</dd>
            <dt>Coverage</dt>
            <dd>{describeCoverage(detail.coverage)}</dd>
            <dt>Alarms</dt>
            {/* An annotation and nothing more: §3.3 warns that "the first station to raise
                an alarm" is circular, so the chain below is computed without these. */}
            <dd>
              {detail.alarms.length === 0
                ? "none overlapping the stop"
                : detail.alarms
                    .map(
                      (alarm) =>
                        `${alarm.station} ${alarm.code} (${alarm.status})`,
                    )
                    .join(" · ")}
            </dd>
          </dl>
          <Chain derivation={detail.derivation} />
        </>
      )}
    </CitationPanel>
  );
}

/** §5.4's chain, drawn as the steps it is.
 *
 * Whole rather than as its verdict, because §6.5 means the agent to be able to contradict
 * it — and a reader can only check a contradiction against reasoning that is on the screen.
 */
function Chain({ derivation }: { derivation: StopDetail["derivation"] }) {
  return (
    <section className="derivation">
      <h4 className="derivation__title">Propagation chain</h4>
      {derivation.links.length === 0 ? (
        <p>
          No chain was walked
          {derivation.unexplained === null
            ? "."
            : `: ${derivation.unexplained.detail}`}
        </p>
      ) : (
        <ol className="derivation__links">
          {derivation.links.map((link) => (
            <li key={`${link.station}:${link.from_ts}`}>
              <span className="mono">{link.station}</span>{" "}
              <StateBadge state={link.state} reason={link.reason} />{" "}
              <span className="derivation__buffer">
                {describeLinkBuffer(link.buffer, link.buffer_condition_since)}
              </span>
            </li>
          ))}
        </ol>
      )}
      <p className="derivation__termination">
        Terminated at the {derivation.termination.replace("_", " ")}
        {derivation.unexplained === null
          ? ""
          : ` — ${derivation.unexplained.detail}`}
        {derivation.cause_candidates.length > 1
          ? `. ${String(derivation.cause_candidates.length)} independent cause candidates, which is what makes this ambiguous rather than resolved.`
          : ""}
      </p>
    </section>
  );
}

/** §5.3's `/alarms/{id}` — the whole lifecycle, which is the point of the record. */
export function AlarmPanel({ identifier }: { identifier: string }) {
  const resolution = useResolution(`alarm:${identifier}`, (token) =>
    fetchAlarm(identifier, token),
  );

  return (
    <CitationPanel what={`alarm ${identifier}`} resolution={resolution}>
      {(alarm) => (
        <dl>
          <dt>Code</dt>
          <dd className="mono">{alarm.code}</dd>
          <dt>Text</dt>
          <dd>{alarm.text}</dd>
          <dt>Station</dt>
          <dd className="mono">{alarm.station}</dd>
          <dt>Severity</dt>
          <dd>{alarm.severity}</dd>
          <dt>Raised</dt>
          <dd className="mono">{alarm.raised_at}</dd>
          {/* Three instants, two of them nullable, and that is what makes the record
              useful: raised and never acknowledged is a different fact from raised,
              acknowledged and still not cleared. */}
          <dt>Acknowledged</dt>
          <dd className="mono">{alarm.acked_at ?? "never acknowledged"}</dd>
          <dt>Cleared</dt>
          <dd className="mono">{alarm.cleared_at ?? "never cleared"}</dd>
          <dt>Status</dt>
          <dd>
            {alarm.status}
            {alarm.active ? " — still standing" : ""}
          </dd>
        </dl>
      )}
    </CitationPanel>
  );
}

/** §6.2's document behind a cited procedure, text and all.
 *
 * §7.2 singles this one out — *"clicking one opens the underlying data, **including the SOP
 * text itself**"* — so the body is rendered whole. A summary here would be the frontend
 * deciding which half of a procedure the reader is allowed to check the answer against.
 */
export function SopPanel({ documentId }: { documentId: string }) {
  const resolution = useResolution(`knowledge:${documentId}`, (token) =>
    fetchKnowledgeDocument(documentId, token),
  );

  return (
    <CitationPanel what={documentId} resolution={resolution}>
      {(document) => (
        <>
          <dl>
            <dt>Document</dt>
            <dd className="mono">{document.id}</dd>
            <dt>Title</dt>
            <dd>{document.title}</dd>
            <dt>Loading</dt>
            <dd>
              {document.always_load
                ? "always loaded, whatever the question"
                : "routed by the question"}
            </dd>
          </dl>
          <pre className="evidence__document">{document.body}</pre>
        </>
      )}
    </CitationPanel>
  );
}

/** §5.3's single-component recall, which is what a `serial` citation refers to.
 *
 * A *component* serial, not an assembly one — RULING M4-R5, recorded in
 * `agent/citations.py`: read literally §7.3's `serial` is `part` spelled twice, and this
 * plant has a second kind of serial with its own endpoint. `agent.citations.resolves`
 * verifies the citation against this same endpoint, so opening it anywhere else could 404
 * on a citation §6.5 had already checked and kept.
 */
export function ComponentPanel({ serial }: { serial: string }) {
  const resolution = useResolution(`component:${serial}`, (token) =>
    fetchComponentAssembly(serial, token),
  );

  return (
    <CitationPanel what={`component ${serial}`} resolution={resolution}>
      {(component) => (
        <dl>
          <dt>Component</dt>
          <dd className="mono">{component.component_serial}</dd>
          <dt>Read at</dt>
          {/* Null is unknown and not absent: a component whose read event lies before the
              gateway's history horizon is one this system knows exists and little else. */}
          <dd className="mono">{component.read_at ?? "unknown"}</dd>
          <dt>Feeder lane</dt>
          <dd>{component.lane ?? "unknown"}</dd>
          <dt>Lot</dt>
          <dd>
            {component.lot === null
              ? "unknown"
              : `${component.lot.lot_code} · ${component.lot.supplier} · loaded ${component.lot.loaded_at}`}
          </dd>
          <dt>Built into</dt>
          <dd>
            {component.assembly_serial === null ? (
              "nothing yet — read at a feeder and not yet built in"
            ) : (
              <PartLinks serials={[component.assembly_serial]} />
            )}
          </dd>
          <dt>Assembly created</dt>
          <dd className="mono">{component.assembly_created_at ?? "unknown"}</dd>
          <dt>Carrier</dt>
          <dd>{component.carrier_id ?? "unknown"}</dd>
          <dt>Left the line</dt>
          <dd>{describeDisposition(component.disposition)}</dd>
        </dl>
      )}
    </CitationPanel>
  );
}

/** Which assemblies carry a component from this lot, and where each of them went. */
export function LotPanel({ lotCode }: { lotCode: string }) {
  const resolution = useResolution(`lot:${lotCode}`, (token) =>
    fetchLotParts(lotCode, token),
  );

  return (
    <CitationPanel what={`lot ${lotCode}`} resolution={resolution}>
      {(lot) => (
        <>
          <dl>
            <dt>Lot</dt>
            <dd className="mono">{lot.lot_code}</dd>
            {/* A list, because a lot code is unique per *lane* and not per line: the same
                code loaded on both feeders is two rows and two populations. */}
            <dt>Loaded</dt>
            <dd>
              {lot.lots.length === 0
                ? "no loading recorded"
                : lot.lots
                    .map(
                      (ref) =>
                        `lane ${String(ref.lane)} · ${ref.supplier} · ${ref.loaded_at}`,
                    )
                    .join(" · ")}
            </dd>
            <dt>Window</dt>
            <dd className="mono">
              {lot.window === null
                ? "all recorded history"
                : `${lot.window.from_ts} → ${lot.window.to_ts}`}
            </dd>
          </dl>
          <Outcomes parts={lot.parts} />
        </>
      )}
    </CitationPanel>
  );
}

/** §5.3's split, and the reason `/parts/affected` exists: *"340 serials, 62 rejected, 278
 * shipped and need checking."* Collapsing the three into a total is what makes a
 * traceability answer useless, so they are never summed here. */
function Outcomes({ parts }: { parts: LotParts["parts"] }) {
  const groups = [
    ["Rejected — already contained", parts.rejected],
    ["Shipped — the ones someone has to act on", parts.shipped],
    ["Still on the line — no disposition yet", parts.on_the_line],
  ] as const;

  return (
    <>
      <p className="evidence__note">{parts.total} assemblies in all.</p>
      <dl>
        {groups.map(([label, group]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>
              {group.count === 0 ? (
                "none"
              ) : (
                <>
                  {group.count}: <PartLinks serials={group.serials} />
                  {group.truncated
                    ? " …the service capped this list, so it is shorter than the count"
                    : ""}
                </>
              )}
            </dd>
          </Fragment>
        ))}
      </dl>
    </>
  );
}

// --- the window-scoped kinds ------------------------------------------------------------

/** §5.5's answer for one cell of one dimension — everything a reader needs to disagree. */
export function PatternPanel({
  dimension,
  value,
  window,
}: {
  dimension: string;
  /** §7.3 spells this `key`, which JSX reserves. The value of the dimension: carrier `7`. */
  value: string;
  /** The interval the claim was made over, as the citation carries it (§7.3). */
  window: CitationWindow;
}) {
  const resolution = useResolution(
    `patterns:${window.from_ts}:${window.to_ts}`,
    (token) => fetchPatterns(window, token),
  );

  return (
    <CitationPanel
      what={`pattern ${dimension}=${value}`}
      resolution={resolution}
    >
      {(report) => {
        const section = report.dimensions.find(
          (candidate) => candidate.dimension === dimension,
        );
        const cell = section?.patterns.find(
          (candidate) => candidate.value === value,
        );
        return (
          <>
            <WindowNote window={window} />
            {section === undefined ? (
              <NotInTheAnswer
                what={`${dimension}=${value}`}
                detail={`the report over this window covers ${report.dimensions
                  .map((candidate) => candidate.dimension)
                  .join(", ")}`}
              />
            ) : section.not_comparable !== null ? (
              // "We looked and found nothing" is a materially different statement from
              // "there is nothing to look at", and §5.5 keeps them apart on purpose.
              <NotInTheAnswer
                what={dimension}
                detail={section.not_comparable}
              />
            ) : cell === undefined ? (
              <NotInTheAnswer
                what={`${dimension}=${value}`}
                detail={`the dimension was computed and holds ${String(section.patterns.length)} value(s), none of them this one`}
              />
            ) : (
              <dl>
                <dt>Verdict</dt>
                <dd>{cell.verdict.replace(/_/g, " ")}</dd>
                <dt>Observed</dt>
                <dd>
                  {cell.observed} of {cell.trials}
                </dd>
                <dt>Observed share</dt>
                <dd>{share(cell.observed_share)}</dd>
                <dt>Expected share</dt>
                <dd>{share(cell.expected_share)}</dd>
                <dt>Effect size</dt>
                <dd>{fixed(cell.effect_size, 3)}</dd>
                {/* The raw p beside the corrected one, because the correction is the step
                    a reader most needs to be able to check. Both are null exactly when the
                    verdict is `not enough data`, where a number would invite a comparison
                    against α that the third verdict exists to prevent. */}
                <dt>p</dt>
                <dd>{fixed(cell.p_value, 4)}</dd>
                <dt>p, corrected</dt>
                <dd>{fixed(cell.adjusted_p_value, 4)}</dd>
                <dt>Measured within</dt>
                <dd>
                  {cell.stratum ?? "the whole window, not a stratum of it"}
                </dd>
              </dl>
            )}
            <p className="evidence__note">
              α {report.alpha}, {report.correction.replace(/_/g, " ")}{" "}
              correction, minimum sample {report.minimum_sample};{" "}
              {report.significant_count} significant cell(s) in the whole
              report. {describeCoverage(report.coverage)}
            </p>
          </>
        );
      }}
    </CitationPanel>
  );
}

/** §5.3's `/signals/trend` for the cited station and signal. */
export function SignalPanel({
  station,
  signal,
  window,
}: {
  station: string;
  signal: string;
  /** The interval the claim was made over, as the citation carries it (§7.3). */
  window: CitationWindow;
}) {
  const resolution = useResolution(
    `trend:${station}:${signal}:${window.from_ts}:${window.to_ts}`,
    (token) => fetchSignalTrend(station, signal, window, token),
  );

  return (
    <CitationPanel what={`${station}/${signal}`} resolution={resolution}>
      {(trend) => (
        <>
          <WindowNote window={window} />
          <dl>
            <dt>Station</dt>
            <dd className="mono">{trend.station}</dd>
            <dt>Signal</dt>
            <dd className="mono">{trend.signal}</dd>
            <dt>Aggregation</dt>
            <dd>
              {trend.bucket_seconds === null
                ? `${trend.agg} — one row per sample`
                : `${trend.agg} — ${trend.bucket_seconds} s buckets, so each value is a mean of several samples and not a measurement`}
            </dd>
          </dl>
          {trend.points.length === 0 ? (
            // §5.3 answers an unknown signal name with an empty series rather than a 404,
            // so "S2 publishes no such signal" and "S2's signal went quiet" arrive here
            // identically. Saying which it is would be inventing the distinction.
            <NotInTheAnswer
              what={`${station}/${signal}`}
              detail="no points over this window — a signal name the station does not publish and a signal that went quiet look the same from here"
            />
          ) : (
            <div className="scroller">
              <table className="trend">
                <caption>
                  {trend.points.length} bucket(s)
                  {trend.truncated
                    ? " — the series was cut short by the service, which is not the same as a signal that stopped"
                    : ""}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">At</th>
                    <th scope="col">Mean</th>
                    <th scope="col">Min</th>
                    <th scope="col">Max</th>
                    <th scope="col">Samples</th>
                  </tr>
                </thead>
                <tbody>
                  {trend.points.map((point) => (
                    <tr key={point.at}>
                      <td className="mono">{point.at}</td>
                      <td>{fixed(point.value, 2)}</td>
                      <td>{fixed(point.min_value, 2)}</td>
                      <td>{fixed(point.max_value, 2)}</td>
                      <td>{point.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </CitationPanel>
  );
}

/** §7.3's containment scope: the serials the claim was made over, each openable.
 *
 * **`/parts/affected` is deliberately not re-queried here.** §7.3 writes the citation as
 * `{ kind: "containment", query, count, serials[] }` and the answer contract carries only
 * `serials` — no query, no window. Calling the endpoint with a window alone would return
 * *every part made in that window*, which is a different set from the cited one and would
 * be handed to a reader as the scope to act on at three in the morning. The cited set is
 * the answer; each serial resolves against `/parts/{serial}`, which is exactly the check
 * §6.5 already ran over it.
 */
export function ContainmentPanel({ serials }: { serials: readonly string[] }) {
  return (
    <div data-testid="evidence-panel" data-outcome="open" className="evidence">
      <p className="evidence__note">
        The scope this claim was made over, as cited: {serials.length} part(s).
        Whether it is the *right* scope — no misses, no false inclusions — is
        scored against ground truth by the harness, not here.
      </p>
      {serials.length === 0 ? (
        <NotInTheAnswer
          what="this containment scope"
          detail="the citation names no serials at all, which the answer validator should have refused"
        />
      ) : (
        <PartLinks serials={serials} />
      )}
    </div>
  );
}

// --- shared bits -------------------------------------------------------------------------

/** Which interval the panel below is a reading of.
 *
 * On screen rather than assumed. Both of these panels show a report computed over a window,
 * and a reader checking one against the answer has to be able to see that the two are the
 * same window — the calendar's own phrasing of it first, since that is what §6.1 has the
 * answer quote back, and the instants after it for anyone who needs to reproduce the query.
 */
function WindowNote({ window }: { window: CitationWindow }) {
  return (
    <p className="evidence__window">
      Opened over the window this claim was made over: {window.label} —{" "}
      <span className="mono">{window.from_ts}</span> →{" "}
      <span className="mono">{window.to_ts}</span>.
    </p>
  );
}

function describeCoverage(coverage: StopDetail["coverage"]): string {
  if (coverage.fully_covered) {
    return "complete — no ingest gap over this interval";
  }
  return (
    `${(coverage.covered_fraction * 100).toFixed(1)} % covered, ` +
    `${String(coverage.gaps.length)} ingest gap(s): an absence here may be the gateway's ` +
    "rather than the line's"
  );
}

function describeLinkBuffer(
  buffer: string | null,
  conditionSince: string | null,
): string {
  if (buffer === null) return "end of the chain";
  // Buffer named, moment not found: the difference between the end of a chain and the end
  // of the evidence, and §5.4 keeps the two apart rather than reporting either as the other.
  return conditionSince === null
    ? `via ${buffer}, whose empty-or-full moment was not found within the lead-in`
    : `via ${buffer}, in that condition since ${conditionSince}`;
}

function describeDisposition(
  disposition: {
    at: string;
    disposition: string;
    reason: string | null;
  } | null,
): string {
  if (disposition === null) return "still on the line";
  const reason = disposition.reason === null ? "" : ` — ${disposition.reason}`;
  return `${disposition.disposition} at ${disposition.at}${reason}`;
}

/** A share as a percentage, or the words for a share that was never computed. Null is "not
 * computed" and is never a zero, which is a distinction §5.5 makes explicitly. */
function share(value: number | null): string {
  return value === null ? "not computed" : `${(value * 100).toFixed(2)} %`;
}

function fixed(value: number | null, digits: number): string {
  return value === null ? "not computed" : value.toFixed(digits);
}
