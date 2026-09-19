/** §7.2's stop timeline, asserted on what a reader sees.
 *
 * *"Station states as a Gantt across the window, propagation chain drawn on it."* The three
 * things that have to be true of that picture are that the episodes speak the colour
 * language §15 chose and never only in colour, that the chain on the axis is the one the
 * service derived — drawn when there is one and said to be absent when there is not — and
 * that a window with nothing in it is rendered as **which** of §4.4's three situations it
 * is, rather than as an empty chart. An empty chart reads as "nothing happened", which is
 * the confident wrong answer this whole system exists to refuse.
 */
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { CitationChip } from "../CitationChip";
import { ENCODINGS } from "../design/stateCategory";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type {
  Coverage,
  PlantStatus,
  StopDetail,
  StopList,
  TimeResolution,
} from "../api";

const TOKEN = "dev-token-123";

const WINDOW = {
  from_ts: "2026-09-12T01:00:00Z",
  to_ts: "2026-09-12T02:00:00Z",
};

/** A window nothing was missed in. Typed against the generated contract, so a field the
 * analysis service renames breaks these fixtures rather than emptying a panel. */
const COMPLETE: Coverage = {
  window: WINDOW,
  gaps: [],
  covered_fraction: 1,
  fully_covered: true,
  observed: { events: 4200, from_ts: WINDOW.from_ts, to_ts: WINDOW.to_ts },
};

/** §4.4's three situations, each as the service reports it. */
const QUIET: Coverage = {
  ...COMPLETE,
  observed: { events: 0, from_ts: null, to_ts: null },
};

const PARTIAL: Coverage = {
  window: WINDOW,
  gaps: [
    {
      from_ts: "2026-09-12T01:20:00Z",
      to_ts: "2026-09-12T01:40:00Z",
      reason: "gateway offline",
    },
  ],
  covered_fraction: 0.667,
  fully_covered: false,
  observed: {
    events: 1200,
    from_ts: WINDOW.from_ts,
    to_ts: "2026-09-12T01:20:00Z",
  },
};

const BLACKOUT: Coverage = {
  window: WINDOW,
  gaps: [{ ...WINDOW, reason: "gateway offline" }],
  covered_fraction: 0,
  fully_covered: false,
  observed: { events: 0, from_ts: null, to_ts: null },
};

/** §5.4's worked example, as the service returns it: S4 starved because B3_4 emptied,
 * B3_4 emptied because S3 was starved, and the walk ends at S2's own fault. */
const DETAIL: StopDetail = {
  stop: {
    id: "stop-20260912T013000.000000Z",
    from_ts: "2026-09-12T01:30:00Z",
    to_ts: "2026-09-12T01:36:00Z",
    duration_seconds: 360,
    started_before_window: false,
    open_at_window_end: false,
    category: "internal",
  },
  as_of: "2026-09-12T02:00:00Z",
  coverage: COMPLETE,
  history_from_ts: "2026-09-12T01:25:00Z",
  timeline: [
    {
      station: "S1",
      state: "Execute",
      from_ts: "2026-09-12T01:25:00Z",
      to_ts: "2026-09-12T01:36:00Z",
      reason: null,
      reason_buffer: null,
    },
    {
      station: "S2",
      state: "Held",
      from_ts: "2026-09-12T01:29:00Z",
      to_ts: "2026-09-12T01:35:00Z",
      reason: null,
      reason_buffer: null,
    },
    {
      station: "S3",
      state: "Suspended",
      from_ts: "2026-09-12T01:29:40Z",
      to_ts: null,
      reason: "starved:B2_3",
      reason_buffer: "B2_3",
    },
    {
      station: "S4",
      state: "Suspended",
      from_ts: "2026-09-12T01:30:10Z",
      to_ts: "2026-09-12T01:36:00Z",
      reason: "starved:B3_4",
      reason_buffer: "B3_4",
    },
  ],
  // §4.1 publishes a level only when a carrier moves through, so these are the movements
  // that emptied B3_4 at 01:29:50 — the instant `derivation.links[0]` names as the reason
  // S4 starved twenty seconds later, and the series that instant is read off.
  buffer_levels: [
    { buffer: "B3_4", at: "2026-09-12T01:29:20Z", level: 2 },
    { buffer: "B3_4", at: "2026-09-12T01:29:50Z", level: 0 },
    { buffer: "B2_3", at: "2026-09-12T01:29:10Z", level: 1 },
    { buffer: "B2_3", at: "2026-09-12T01:29:40Z", level: 0 },
  ],
  alarms: [],
  derivation: {
    category: "internal",
    termination: "cause_candidate",
    cause_candidates: [],
    unexplained: null,
    links: [
      {
        station: "S4",
        state: "Suspended",
        from_ts: "2026-09-12T01:30:10Z",
        to_ts: "2026-09-12T01:36:00Z",
        reason: "starved:B3_4",
        buffer: "B3_4",
        buffer_condition_since: "2026-09-12T01:29:50Z",
      },
      {
        station: "S2",
        state: "Held",
        from_ts: "2026-09-12T01:29:00Z",
        to_ts: "2026-09-12T01:35:00Z",
        reason: null,
        buffer: null,
        buffer_condition_since: null,
      },
    ],
  },
};

/** The same stop, with the walk having run out. §5.4 keeps "the chain ended" and "the
 * evidence ended" apart, and so must the screen. */
const NO_CHAIN: StopDetail = {
  ...DETAIL,
  derivation: {
    category: null,
    termination: "unexplained",
    cause_candidates: [],
    unexplained: {
      station: "S3",
      buffer: "B2_3",
      detail: "no episode explains B2_3 running empty within the lead-in",
    },
    links: [],
  },
};

function listOf(coverage: Coverage, stops: StopList["stops"] = []): StopList {
  return {
    window: WINDOW,
    coverage,
    stops,
    micro_stops: 3,
    micro_stop_threshold_seconds: 60,
    truncated: false,
  };
}

const RESOLUTION: TimeResolution = {
  expression: "last night",
  window: WINDOW,
  label: "last night 2026-09-12 01:00 – 02:00 UTC",
  closed: true,
  now: "2026-09-12T06:00:00Z",
};

/** What the shell's plant status banner reads.
 *
 * Answered separately from this view's endpoints, and that is not tidiness: the banner
 * stands over every view now, so a stub that handed it a stop list — or a refusal meant for
 * `/time/resolve` — would put this view's own sentences on the screen a second time, under a
 * heading about the plant.
 */
const PLANT_STATUS: PlantStatus = {
  state: "live",
  lastEventSourceTs: WINDOW.to_ts,
  backfillProgress: 1,
  queueDepth: 0,
  overflowCount: 0,
  clockAvailable: true,
};

/** Every endpoint this view reaches, answering with the fixtures above. */
function stub(bodies: {
  list?: StopList;
  detail?: StopDetail;
  resolution?: TimeResolution;
}): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((url: string) => {
    const body = url.startsWith("/api/gateway")
      ? PLANT_STATUS
      : url.includes("/time/resolve")
        ? (bodies.resolution ?? RESOLUTION)
        : /\/stops\/[^?]/.test(url)
          ? (bodies.detail ?? DETAIL)
          : (bodies.list ?? listOf(COMPLETE));
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Which URLs this view asked the **analysis service** for.
 *
 * The shell stands a plant status banner over every view, and it reads the edge gateway —
 * so `fetch` is called on arrival at any address and "nothing was read" can no longer be a
 * statement about `fetch`. What these tests pin is that no window means no history is read,
 * which is a claim about the service the history lives in.
 */
function readFromAnalysis(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.startsWith("/api/analysis"));
}

function at(address: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[address]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
}

/** The drawn chart, once Vega has run. */
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

// --- the episodes --------------------------------------------------------------------------

test("the episodes are drawn in the five categories, and never only in colour", async () => {
  // §15 and ISA-101: the station that *is* the problem has to read differently from the
  // ones merely waiting on it, on a greyscale print and read aloud as well as in colour.
  stub({});

  at("/timeline?stop=stop-20260912T013000.000000Z");
  const svg = await drawn();
  const text = svg.textContent ?? "";

  expect(text).toContain(ENCODINGS["held-by-own-fault"].label);
  expect(text).toContain(ENCODINGS["waiting-on-others"].label);
  expect(text).toContain(ENCODINGS["held-by-own-fault"].glyph);
  expect(text).toContain(ENCODINGS.producing.glyph);
  // Every station the service returned episodes for is a row, so a station that went quiet
  // is visibly on the chart rather than missing from it.
  for (const station of ["S1", "S2", "S3", "S4"]) {
    expect(text).toContain(station);
  }
});

test("an episode with no end is not drawn as one that was observed to end", async () => {
  // `to_ts` is null while the station was still in that state at the end of the history
  // read — not "until now". A bar has to stop somewhere; what must not happen is that
  // stopping reading as an observation.
  stub({});

  at("/timeline?stop=stop-20260912T013000.000000Z");
  await drawn();

  expect(
    await screen.findByText(
      "still in this state at the end of the history read",
    ),
  ).toBeInTheDocument();
});

// --- the chain ------------------------------------------------------------------------------

test("a derivation with a chain is drawn on the same axis as the episodes", async () => {
  // The whole point of the view: §5.4's derivation is returned as data so that it can be
  // contradicted, and it can only be checked against the episodes it was walked over if it
  // is on the same ruler as them.
  stub({});

  at("/timeline?stop=stop-20260912T013000.000000Z");
  const svg = await drawn();
  const text = svg.textContent ?? "";

  // Each step, in the order §5.4 walked it, labelled with the buffer that justifies it.
  expect(text).toContain("1. via B3_4");
  expect(text).toContain("2. root");
  // And the walk is in the chart's own description, so a reader who cannot see it is told
  // what it derives rather than that a picture exists.
  expect(
    screen.getByText(/The propagation chain runs S4 Suspended ← S2 Held/),
  ).toBeInTheDocument();
});

test("a derivation with no chain says so rather than drawing a plausible one", async () => {
  stub({ detail: NO_CHAIN });

  at("/timeline?stop=stop-20260912T013000.000000Z");
  const svg = await drawn();

  expect(svg.textContent ?? "").not.toContain("1. via");
  expect(screen.getByText(/No chain was walked/)).toBeInTheDocument();
  expect(
    screen.getByText(/No propagation chain was derived for this stop/),
  ).toBeInTheDocument();
  // The episodes are still there: "no chain" is a fact about the derivation, not about
  // whether the line has a state history.
  expect(svg.textContent ?? "").toContain("S4");
});

test("a stop the service returned no episodes for says so instead of drawing axes", async () => {
  stub({ detail: { ...DETAIL, timeline: [] } });

  at("/timeline?stop=stop-20260912T013000.000000Z");

  expect(
    await screen.findByText(/returned no state episodes for this stop/),
  ).toBeInTheDocument();
  expect(screen.queryByTestId("chart-canvas")).not.toBeInTheDocument();
});

// --- the buffer levels, which are what the chain was walked over ------------------------------

test("the buffer levels are drawn under the episodes, on the same time axis", async () => {
  // §5.4 derives the chain *from* buffer levels, and `StopDetail` calls them "the second row
  // of the same chart… what makes a chain's `buffer_condition_since` checkable by eye". The
  // dashed rule at 01:29:50 is the walk's conclusion; this row is the evidence for it, and
  // a reader can only check one against the other if both are on one ruler.
  stub({});

  at("/timeline?stop=stop-20260912T013000.000000Z");
  const svg = await drawn();
  const text = svg.textContent ?? "";

  // Each buffer named on the chart itself and not only in a legend: colour is never the
  // only channel on this screen.
  expect(text).toContain("B3_4");
  expect(text).toContain("B2_3");
  // The scale the levels are read off — carriers, not station state, and its own axis.
  expect(text).toContain("carriers");
  // And Vega drew the whole thing: a two-row specification it refused would render its
  // refusal here instead, which is the one way a second row quietly becomes no second row.
  expect(screen.queryByTestId("chart-failure")).toBeNull();
  expect(
    screen.getByText(/4 buffer level reading\(s\) for 2 buffer\(s\)/),
  ).toBeInTheDocument();
});

test("a stop with no level published draws no second row rather than an empty one", async () => {
  // §4.1 publishes a level only when a carrier moves through, so a stop with none published
  // is a fact about the line. A row of axes with nothing between them would read as every
  // buffer sitting at zero, which is the opposite reading of the same silence.
  stub({ detail: { ...DETAIL, buffer_levels: [] } });

  at("/timeline?stop=stop-20260912T013000.000000Z");
  const svg = await drawn();

  expect(svg.textContent ?? "").not.toContain("carriers");
  expect(screen.getByText(/No buffer level was published/)).toBeInTheDocument();
  // The episodes are still drawn: "no level was published" is a fact about the buffers,
  // not about the state history.
  expect(svg.textContent ?? "").toContain("S4");
});

test("a two-row chart is drawn at the width of its panel, not at Vega-Lite's default", async () => {
  // Vega-Lite honours a top-level width on a single or layered view and ignores it on a
  // stack of rows, so a chart that grew a second row would silently shrink to a default
  // nobody chose — narrower than the panel, with the same axes and no failure anywhere.
  stub({});

  at("/timeline?stop=stop-20260912T013000.000000Z");
  const svg = await drawn();

  // The element reports no width in jsdom, so the chart falls back to its design width —
  // what matters is that the drawing comes to it rather than to Vega-Lite's own 300.
  expect(Number(svg.getAttribute("width"))).toBe(640);
});

// --- §4.4's three situations -----------------------------------------------------------------

const SITUATIONS: [string, Coverage, RegExp][] = [
  ["a quiet line", QUIET, /the line was quiet, rather than unobserved/],
  ["a blackout", BLACKOUT, /ingest was down for the whole of this interval/],
  [
    "a partly trustworthy window",
    PARTIAL,
    /part of this interval is trustworthy and part is not/,
  ],
];

test.each(SITUATIONS)(
  "a window with no stops in it reads as %s rather than as an empty chart",
  async (_situation, coverage, sentence) => {
    // §4.4: without gap markers, missing data is indistinguishable from a quiet machine.
    // Three situations, three sentences — and never the same blank region.
    stub({ list: listOf(coverage) });

    at(`/timeline?from=${WINDOW.from_ts}&to=${WINDOW.to_ts}`);

    expect(await screen.findByText(sentence)).toBeInTheDocument();
    expect(screen.getByTestId("no-stops")).toBeInTheDocument();
  },
);

test("a window with an ingest gap says where the data is not", async () => {
  // The instants, not a percentage: a reader deciding whether to trust an absence has to
  // know which part of the window it falls in.
  stub({ list: listOf(PARTIAL) });

  at(`/timeline?from=${WINDOW.from_ts}&to=${WINDOW.to_ts}`);

  expect(await screen.findByText("2026-09-12T01:20:00Z")).toBeInTheDocument();
  expect(screen.getByText(/gateway offline/)).toBeInTheDocument();
});

test("a gap over the stop itself is shaded on the chart, not left off it", async () => {
  stub({ detail: { ...DETAIL, coverage: PARTIAL } });

  at("/timeline?stop=stop-20260912T013000.000000Z");
  await drawn();

  expect(screen.getByText(/1 ingest gap\(s\) are shaded/)).toBeInTheDocument();
  // And the band really is on the chart: a layer Vega refused would render its refusal
  // here instead, which is the one way a shaded gap can quietly become an unshaded one.
  expect(screen.queryByTestId("chart-failure")).toBeNull();
});

// --- the window ------------------------------------------------------------------------------

test("no window means nothing is read", async () => {
  // A default window would be a reading of an interval nobody asked about, under a heading
  // that does not say so.
  const fetchMock = stub({});

  at("/timeline");

  expect(await screen.findByText(/No window yet/)).toBeInTheDocument();
  expect(readFromAnalysis(fetchMock)).toEqual([]);
});

test("a phrase is resolved by the shift calendar and not by the browser", async () => {
  // §5.3 puts `/time/resolve` in the contract so that "last night" is never guessed. A
  // frontend doing the arithmetic itself would be the same guess one layer out, with the
  // shift boundaries and the DST transitions nowhere in sight.
  const fetchMock = stub({});

  at("/timeline");
  fireEvent.change(
    screen.getByLabelText("A phrase the shift calendar understands"),
    { target: { value: "last night" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Resolve" }));

  await waitFor(() => {
    expect(readFromAnalysis(fetchMock)[0]).toContain(
      "/time/resolve?expression=last+night",
    );
  });
  // And the window it resolved to is the one the stops are then read over.
  await waitFor(() => {
    const requested = fetchMock.mock.calls.map((call) =>
      decodeURIComponent(call[0] as string),
    );
    expect(
      requested.some(
        (url) => url.includes("/stops?") && url.includes(WINDOW.from_ts),
      ),
    ).toBe(true);
  });
});

test("a phrase the calendar does not understand is answered with the ones it does", async () => {
  // The service refuses rather than guessing, and puts every expression that would have
  // worked in the refusal. Collapsing that into "422" would throw away the half a reader
  // can act on.
  // By URL: the refusal belongs to `/time/resolve`, and handing it to the shell's plant
  // status banner as well would put the list of understood phrases on the screen twice.
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        url.startsWith("/api/gateway")
          ? new Response(JSON.stringify(PLANT_STATUS), {
              headers: { "Content-Type": "application/json" },
            })
          : new Response(
              JSON.stringify({
                detail: {
                  message:
                    "no understood time expression matches 'yesterdayish'",
                  understood: ["last night", "last shift", "yesterday"],
                },
              }),
              { status: 422, headers: { "Content-Type": "application/json" } },
            ),
      ),
    ),
  );

  at("/timeline");
  fireEvent.change(
    screen.getByLabelText("A phrase the shift calendar understands"),
    { target: { value: "yesterdayish" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Resolve" }));

  expect(
    await screen.findByText(/last night; last shift; yesterday/),
  ).toBeInTheDocument();
});

test("something that is not an instant is refused rather than parsed loosely", async () => {
  const fetchMock = stub({});

  at("/timeline");
  fireEvent.change(screen.getByLabelText("From (UTC)"), {
    target: { value: "last tuesday-ish" },
  });
  fireEvent.change(screen.getByLabelText("To (UTC)"), {
    target: { value: WINDOW.to_ts },
  });
  fireEvent.click(screen.getByRole("button", { name: "Show stops" }));

  expect(await screen.findByText(/is not an instant/)).toBeInTheDocument();
  expect(readFromAnalysis(fetchMock)).toEqual([]);
});

test("choosing a stop reads that stop, and the window stays in the address", async () => {
  const fetchMock = stub({ list: listOf(COMPLETE, [DETAIL.stop]) });

  at(`/timeline?from=${WINDOW.from_ts}&to=${WINDOW.to_ts}`);
  fireEvent.click(await screen.findByRole("button", { name: /360.0 s/ }));

  await drawn();
  const requested = fetchMock.mock.calls.map((call) => call[0] as string);
  expect(
    requested.some((url) =>
      url.includes(`/stops/${encodeURIComponent(DETAIL.stop.id ?? "")}`),
    ),
  ).toBe(true);
  // The list is still on the screen and still over the same window: the chart is a second
  // reading of one interval, not a move to another.
  expect(screen.getByLabelText("From (UTC)")).toHaveValue(WINDOW.from_ts);
});

test("a stop with no id is not offered as something to open", async () => {
  // §5.3: the window opens before the history does, so this stop's start is the caller's
  // own boundary and there is no instant `/stops/{id}` could verify. A button here would
  // open onto a 404.
  stub({
    list: listOf(COMPLETE, [{ ...DETAIL.stop, id: null }]),
  });

  at(`/timeline?from=${WINDOW.from_ts}&to=${WINDOW.to_ts}`);

  expect(
    await screen.findByText(/began before the history did/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /360.0 s/ })).toBeNull();
});

// --- identity and the way in -------------------------------------------------------------------

test("every request carries the reader's own token", async () => {
  // §10.5: every diagnostics endpoint refuses a request without one, and a view that read
  // the line's history without an identity on it would be a hole the services then close.
  const fetchMock = stub({});

  at("/timeline?stop=stop-20260912T013000.000000Z");
  await drawn();

  for (const call of fetchMock.mock.calls) {
    const init = call[1] as RequestInit | undefined;
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      `Bearer ${TOKEN}`,
    );
  }
});

test("a stop citation opens this view at its own stop", async () => {
  // The interval a question resolved to, carried across: the id resolves to the same stop
  // whatever window it was cited from, so the link lands on the stop's own window rather
  // than on a recent one the screen would have had to pick.
  stub({});

  const view = render(
    <AuthProvider>
      <MemoryRouter>
        <CitationChip
          citation={{ kind: "stop", id: "stop-20260912T013000.000000Z" }}
        />
      </MemoryRouter>
    </AuthProvider>,
  );
  fireEvent.click(
    within(view.container).getAllByRole("button")[0] as HTMLElement,
  );

  const link = await screen.findByRole("link", {
    name: /Open this stop on the timeline/,
  });
  expect(link).toHaveAttribute(
    "href",
    "/timeline?stop=stop-20260912T013000.000000Z",
  );
});
