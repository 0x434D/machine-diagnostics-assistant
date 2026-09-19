/** §7.2's containment view: the affected set, with an export.
 *
 * *"340 serials, 62 rejected, 278 shipped and need checking."* This is where that stops
 * being a call to `/parts/affected` and becomes the list somebody works from at three in the
 * morning — which is why the export below is the set the query actually returned and says
 * its own counts. An export that silently differs from what was on screen is worse than no
 * export: the screen gets checked and the file gets acted on.
 *
 * Nothing here computes anything. The counts, the split and the window are the service's;
 * the file is a transcription of the response object this screen rendered, and it is built
 * from that same object rather than from the DOM or from a second request.
 */
import { useEffect, useState } from "react";

import {
  fetchAffectedParts,
  type AffectedParts,
  type ContainmentCriterion,
} from "../api";
import { CitationPanel } from "../citations/CitationPanel";
import { useResolution } from "../citations/useResolution";
import { Outcomes } from "../parts/Outcomes";

/** The criteria a reader can fill in, and how each is asked for. `number` is not a nicety:
 * a carrier or a tolerance typed as text reaches the service as a 422 about the parameter
 * rather than an answer about the line. */
const CRITERIA: {
  name: ContainmentCriterion;
  label: string;
  numeric: boolean;
}[] = [
  { name: "station", label: "Station", numeric: false },
  { name: "carrier", label: "Carrier", numeric: true },
  { name: "lot", label: "Lot code", numeric: false },
  { name: "defect_class", label: "Defect class", numeric: false },
  { name: "signal", label: "Process value", numeric: false },
  { name: "below", label: "…below", numeric: true },
  { name: "above", label: "…above", numeric: true },
];

interface Query {
  window: { from: string; to: string };
  criteria: Partial<Record<ContainmentCriterion, string>>;
}

export function Containment() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [asked, setAsked] = useState<Query | null>(null);

  const complete = INSTANT.test(from.trim()) && INSTANT.test(to.trim());

  return (
    <section className="containment">
      <h2>Containment</h2>
      <p className="containment__lede">
        Which parts a condition touched, and where each of them went. The
        criteria conjoin, and every one of them is optional — a window on its
        own is every part made in that window, which is where a shift-wide
        containment starts.
      </p>

      <form
        className="containment__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!complete) return;
          setAsked({
            window: { from: asInstant(from), to: asInstant(to) },
            criteria: filled(typed),
          });
        }}
      >
        {/* Typed as the UTC instant it is, not picked from a `datetime-local` widget. That
            widget shows the reader's *local* calendar, and every other instant in this
            application is the UTC string a service wrote — so the picker would quietly
            convert a window a reader had copied out of a stop panel. Here the two forms are
            the same characters. */}
        <div className="containment__field">
          <label htmlFor="from">From, UTC</label>
          <input
            id="from"
            name="from"
            type="text"
            inputMode="numeric"
            placeholder="2026-09-12T01:00:00Z"
            autoComplete="off"
            value={from}
            onChange={(event) => {
              setFrom(event.target.value);
            }}
          />
        </div>
        <div className="containment__field">
          <label htmlFor="to">To, UTC</label>
          <input
            id="to"
            name="to"
            type="text"
            inputMode="numeric"
            placeholder="2026-09-12T02:00:00Z"
            autoComplete="off"
            value={to}
            onChange={(event) => {
              setTo(event.target.value);
            }}
          />
        </div>

        {CRITERIA.map((criterion) => (
          <div className="containment__field" key={criterion.name}>
            <label htmlFor={criterion.name}>{criterion.label}</label>
            <input
              id={criterion.name}
              name={criterion.name}
              type={criterion.numeric ? "number" : "text"}
              step={criterion.numeric ? "any" : undefined}
              autoComplete="off"
              value={typed[criterion.name] ?? ""}
              onChange={(event) => {
                setTyped({ ...typed, [criterion.name]: event.target.value });
              }}
            />
          </div>
        ))}

        <button type="submit" disabled={!complete}>
          Find the affected set
        </button>
      </form>

      {/* No default window, and that is deliberate. All analysis reads `SourceTimestamp`,
          which is simulated time and can sit hours either side of the wall clock this
          browser has; a pre-filled "last 24 hours" would look like a sensible default and
          return a set from the wrong interval, which is the one mistake a containment
          screen must not make quietly. */}
      {asked === null ? (
        <p className="containment__note">
          Give a window to search, as a UTC instant —{" "}
          <span className="mono">2026-09-12T01:00:00Z</span>. It is not
          pre-filled: the clock every analysis reads is the line&apos;s
          simulated one, which can sit hours either side of this browser&apos;s,
          so a default window here would look sensible and answer over the wrong
          interval.
        </p>
      ) : (
        <Affected key={keyFor(asked)} query={asked} />
      )}
    </section>
  );
}

function Affected({ query }: { query: Query }) {
  const resolution = useResolution(keyFor(query), (token) =>
    fetchAffectedParts(query.window, query.criteria, token),
  );

  return (
    <CitationPanel what="this containment scope" resolution={resolution}>
      {(affected) => (
        <>
          <p className="evidence__window">
            Over <span className="mono">{affected.window.from_ts}</span> →{" "}
            <span className="mono">{affected.window.to_ts}</span>, applied to
            the <strong>{affected.criteria.anchor}</strong> instant of each
            part. {affected.criteria.window_selects}
          </p>
          <dl>
            <dt>Criteria</dt>
            <dd>{describeCriteria(affected)}</dd>
            <dt>Could not be placed in the window</dt>
            {/* Reported rather than dropped: these match every criterion, and the instant
                the window would be applied to is null for them. A containment list that is
                silently short is worse than one that says it is. */}
            <dd>
              {affected.unplaceable === 0
                ? "none — every matching part had an instant to place it by"
                : `${affected.unplaceable} part(s) match the criteria and have no ${affected.criteria.anchor} instant, so they are outside the counts below rather than absent from the line`}
            </dd>
          </dl>
          <Outcomes parts={affected.parts} />
          <ExportLink affected={affected} />
        </>
      )}
    </CitationPanel>
  );
}

/** The file, as a link whose target is built from the rendered response.
 *
 * A link rather than a button that assembles something on click: the href *is* the set on
 * the screen, derived from the one object both this and `Outcomes` above read, so the two
 * cannot come apart. The row count is in the link's own words and in the file, because
 * §7.2's requirement is that the export carries its own count.
 */
function ExportLink({ affected }: { affected: AffectedParts }) {
  const csv = containmentCsv(affected);
  const [href, setHref] = useState<string | null>(null);

  useEffect(() => {
    const created = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
    );
    setHref(created);
    return () => {
      // A blob URL that outlives its link pins the bytes for the life of the document, and
      // a reader trying six containment queries in a row creates one per query.
      URL.revokeObjectURL(created);
      setHref(null);
    };
  }, [csv]);

  if (href === null) return null;

  const rows = listedSerials(affected).length;
  // From the service's own flags rather than from `rows !== total`. The two agree today —
  // `total` is the sum of the three counts — but a count arithmetic disagreed with would be
  // this screen telling a reader its list was capped when it was not, or worse the reverse.
  const capped = [
    affected.parts.rejected,
    affected.parts.shipped,
    affected.parts.on_the_line,
  ].some((group) => group.truncated);

  return (
    <p className="containment__export">
      <a href={href} download={fileName(affected)}>
        Export this set — {rows} row(s)
      </a>{" "}
      {capped
        ? `of the ${affected.parts.total} in the scope — the service capped at least one of the lists, and the file says which`
        : "the whole scope"}
    </p>
  );
}

/** Every serial this view is showing, in the order the groups are shown in.
 *
 * The file and the screen read the same function over the same object, which is what makes
 * "the export is the set the query actually returned" true by construction rather than by
 * two pieces of code agreeing.
 */
function listedSerials(affected: AffectedParts): [string, string][] {
  return [
    ...affected.parts.rejected.serials.map((serial): [string, string] => [
      serial,
      "rejected",
    ]),
    ...affected.parts.shipped.serials.map((serial): [string, string] => [
      serial,
      "shipped",
    ]),
    ...affected.parts.on_the_line.serials.map((serial): [string, string] => [
      serial,
      "on_the_line",
    ]),
  ];
}

/** The export: a preamble that states every count, then one row per serial.
 *
 * The preamble is `#`-prefixed so a spreadsheet shows it as visible leading rows rather
 * than hiding it, and so an empty scope still produces a file that says "0". `count` is
 * exact and `serials` is capped, so the two are reported separately for each group: a list
 * of 200 under a count of 278 is a true answer, and a file that showed only the 200 would be
 * a false one.
 */
function containmentCsv(affected: AffectedParts): string {
  const parts = affected.parts;
  const rows = listedSerials(affected);
  const groups = [
    ["rejected", parts.rejected],
    ["shipped", parts.shipped],
    ["on the line", parts.on_the_line],
  ] as const;

  const lines = [
    `# containment export`,
    `# window: ${affected.window.from_ts} to ${affected.window.to_ts}, applied to the ${affected.criteria.anchor} instant of each part`,
    `# window selects: ${affected.criteria.window_selects}`,
    `# criteria: ${describeCriteria(affected)}`,
    `# scope: ${affected.parts.total} assemblies — ${groups
      .map(([label, group]) => `${label} ${group.count}`)
      .join(", ")}`,
    `# in this file: ${rows.length} row(s) — ${groups
      .map(
        ([label, group]) =>
          `${label} ${group.serials.length} of ${group.count}${group.truncated ? " (the service capped this list)" : ""}`,
      )
      .join(", ")}`,
    `# outside the scope above: ${affected.unplaceable} part(s) matched the criteria with no ${affected.criteria.anchor} instant to place them by`,
    "assembly_serial,outcome",
    ...rows.map(([serial, outcome]) => `${field(serial)},${outcome}`),
  ];
  return `${lines.join("\n")}\n`;
}

/** One CSV field, quoted if it holds anything that would end it early.
 *
 * Serials on this line hold neither a comma nor a quote, so today this changes nothing. It
 * is here because the failure it prevents is silent and specific to this file: a serial that
 * split across two columns is a row a spreadsheet reads as a different part, in the one
 * artefact of this application somebody acts on away from the screen that produced it.
 */
function field(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

/** Counts in the name too, so a file separated from this screen still carries them. */
function fileName(affected: AffectedParts): string {
  const rows = listedSerials(affected).length;
  return `containment-${String(rows)}-of-${String(affected.parts.total)}-parts.csv`;
}

/** What the service says it was asked, rather than what this form thinks it sent.
 *
 * `AppliedCriteria` is the endpoint echoing the query back, and it is the copy worth
 * showing: a criterion this form spelled wrongly is ignored by the service and would be
 * missing here, where reprinting the form's own state would show it as applied. */
function describeCriteria(affected: AffectedParts): string {
  const applied = affected.criteria;
  const named: string[] = [];

  function add(label: string, value: string | number | null): void {
    if (value !== null) named.push(`${label} ${String(value)}`);
  }

  add("station", applied.station);
  add("carrier", applied.carrier);
  add("lot", applied.lot_code);
  add("defect class", applied.defect_class);
  add("process value", applied.signal);
  add("below", applied.below);
  add("above", applied.above);
  return named.length === 0 ? "the window alone" : named.join(" · ");
}

/** The shapes of a UTC instant this form takes, which is what the rest of the screen prints
 * plus the two abbreviations of it a person types by hand. The submit stays disabled until
 * both fields match one, so the service is never asked to parse a half-typed instant and
 * answer with a validation error about a query parameter. */
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z?$/;

/** What the reader typed, in the form `/parts/affected` takes. */
function asInstant(typed: string): string {
  const instant = typed.trim();
  if (instant.endsWith("Z")) return instant;
  return `${instant}${instant.length === 16 ? ":00" : ""}Z`;
}

function filled(
  typed: Record<string, string>,
): Partial<Record<ContainmentCriterion, string>> {
  const criteria: Partial<Record<ContainmentCriterion, string>> = {};
  for (const { name } of CRITERIA) {
    const value = typed[name]?.trim() ?? "";
    if (value !== "") criteria[name] = value;
  }
  return criteria;
}

/** What has to change for the set to be fetched again. */
function keyFor(query: Query): string {
  return `affected:${query.window.from}:${query.window.to}:${JSON.stringify(query.criteria)}`;
}
