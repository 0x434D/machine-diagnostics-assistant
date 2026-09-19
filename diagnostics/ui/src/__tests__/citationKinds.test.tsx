/** §7.2, for the eight kinds that could not be opened before M6.
 *
 * *"Clicking one opens the underlying data, including the SOP text itself — a citation you
 * cannot open is barely a citation."* What is asserted here is the behaviour a reader sees:
 * that every kind in the contract's own vocabulary opens onto a record, that one that does
 * not resolve says so and says which failure it was, and that nothing goes out without the
 * reader's identity on it.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// Vite's `?raw` suffix reads the generated file as text, so the enumeration below runs
// against the union `json2ts` wrote rather than against a copy of it kept here.
import generatedAnswerSource from "../generated/answer.ts?raw";
import { AuthProvider } from "../AuthContext";
import { CitationChip } from "../CitationChip";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type { Citation } from "../generated/answer";
import type {
  Alarm,
  ComponentAssembly,
  KnowledgeDocument,
  LotParts,
  Part,
  PatternReport,
  SignalTrend,
  StopDetail,
} from "../api";

const TOKEN = "dev-token-123";

// --- what each endpoint answers with ------------------------------------------------------
//
// Typed against the generated contract, so a field the analysis service renames breaks these
// fixtures at compile time rather than letting a panel silently render nothing for it.

const PART: Part = {
  assembly_serial: "A-00000007",
  created_at: "2026-09-12T01:30:01Z",
  carrier_id: 4,
  genealogy: [],
  process_values: [],
  process_curves: [],
  inspection: null,
  disposition: null,
};

const COVERAGE: StopDetail["coverage"] = {
  covered_fraction: 1,
  fully_covered: true,
  gaps: [],
  observed: {
    events: 600,
    from_ts: "2026-09-12T01:00:00Z",
    to_ts: "2026-09-12T02:00:00Z",
  },
  window: { from_ts: "2026-09-12T01:00:00Z", to_ts: "2026-09-12T02:00:00Z" },
};

const STOP: StopDetail = {
  stop: {
    id: "stop-20260912T013000.000000Z",
    from_ts: "2026-09-12T01:30:00Z",
    to_ts: "2026-09-12T01:35:00Z",
    duration_seconds: 300,
    started_before_window: false,
    open_at_window_end: false,
    category: "external_upstream",
  },
  as_of: "2026-09-12T02:00:00Z",
  coverage: COVERAGE,
  history_from_ts: "2026-09-12T01:20:00Z",
  timeline: [],
  buffer_levels: [],
  alarms: [],
  derivation: {
    category: "external_upstream",
    termination: "cause_candidate",
    cause_candidates: [],
    unexplained: null,
    links: [
      {
        station: "S4",
        state: "Suspended",
        from_ts: "2026-09-12T01:30:10Z",
        to_ts: "2026-09-12T01:35:00Z",
        reason: "starved:B3_4",
        buffer: "B3_4",
        buffer_condition_since: "2026-09-12T01:29:40Z",
      },
      {
        station: "S2",
        state: "Held",
        from_ts: "2026-09-12T01:29:00Z",
        to_ts: "2026-09-12T01:34:00Z",
        reason: null,
        buffer: null,
        buffer_condition_since: null,
      },
    ],
  },
};

const ALARM: Alarm = {
  id: 207,
  station: "S2",
  code: "A-207",
  text: "joining force out of tolerance",
  severity: 2,
  raised_at: "2026-09-12T01:28:00Z",
  acked_at: "2026-09-12T01:29:30Z",
  cleared_at: null,
  status: "acknowledged",
  active: true,
};

const SOP_BODY =
  "1. Read the state timeline before the buffers.\n2. Name the root, or say there is none.";

const DOCUMENT: KnowledgeDocument = {
  id: "SOP-01",
  title: "Line stop — first response",
  always_load: true,
  applies_to: {
    question_types: ["line_stop"],
    defect_classes: [],
    dimensions: [],
    stations: [],
    alarm_codes: [],
  },
  body: SOP_BODY,
};

const COMPONENT: ComponentAssembly = {
  component_serial: "C-000123",
  lane: 2,
  read_at: "2026-09-12T01:29:55Z",
  lot: {
    id: 11,
    lot_code: "L-4471",
    lane: 2,
    supplier: "Nordwerk",
    loaded_at: "2026-09-12T00:10:00Z",
  },
  assembly_serial: "A-00000007",
  assembly_created_at: "2026-09-12T01:30:01Z",
  carrier_id: 4,
  disposition: null,
};

const LOT: LotParts = {
  lot_code: "L-4471",
  lots: [
    {
      id: 11,
      lot_code: "L-4471",
      lane: 2,
      supplier: "Nordwerk",
      loaded_at: "2026-09-12T00:10:00Z",
    },
  ],
  parts: {
    total: 3,
    rejected: { count: 1, serials: ["A-00000007"], truncated: false },
    shipped: {
      count: 2,
      serials: ["A-00000008", "A-00000009"],
      truncated: false,
    },
    on_the_line: { count: 0, serials: [], truncated: false },
  },
  window: null,
};

const PATTERNS: PatternReport = {
  alpha: 0.05,
  correction: "benjamini_hochberg",
  coverage: COVERAGE,
  defect_class_threshold: 0.5,
  minimum_sample: 30,
  significant_count: 1,
  window: { from_ts: "2026-09-12T01:00:00Z", to_ts: "2026-09-12T02:00:00Z" },
  dimensions: [
    {
      dimension: "carrier",
      comparable: true,
      not_comparable: null,
      within: null,
      unattributed: 0,
      patterns: [
        {
          value: "7",
          observed: 18,
          trials: 60,
          observed_share: 0.3,
          expected_share: 0.05,
          effect_size: 0.25,
          p_value: 0.0001,
          adjusted_p_value: 0.0006,
          stratum: null,
          verdict: "significant",
        },
      ],
    },
  ],
};

const TREND: SignalTrend = {
  station: "S2",
  signal: "JoiningForcePeak",
  agg: "hour",
  bucket_seconds: 3600,
  truncated: false,
  window: { from_ts: "2026-09-12T01:00:00Z", to_ts: "2026-09-12T02:00:00Z" },
  points: [
    {
      at: "2026-09-12T01:00:00Z",
      value: 904.5,
      min_value: 900,
      max_value: 909,
      count: 600,
    },
  ],
};

/** One minimally valid citation per kind, keyed by the generated union so a kind added to
 * `contracts/answer.schema.json` fails to compile here until it has one. The payload each
 * carries is the payload `agent.answer.CITATION_FIELDS` requires of it. */
const SAMPLES: Record<Citation["kind"], Citation> = {
  part: { kind: "part", id: "A-00000007" },
  stop: { kind: "stop", id: "stop-20260912T013000.000000Z" },
  alarm: { kind: "alarm", id: "207" },
  signal: { kind: "signal", station: "S2", signal: "JoiningForcePeak" },
  pattern: { kind: "pattern", dimension: "carrier", key: "7" },
  sop: { kind: "sop", id: "SOP-01" },
  serial: { kind: "serial", id: "C-000123" },
  lot: { kind: "lot", id: "L-4471" },
  containment: { kind: "containment", serials: ["A-00000007"] },
};

/** The kinds the contract declares, read out of the generated union rather than listed. */
function declaredKinds(): string[] {
  const declaration = /export type Kind = (.+);/.exec(generatedAnswerSource);
  const union = declaration?.[1];
  if (union === undefined) {
    throw new Error("generated/answer.ts has no `Kind` union to check against");
  }
  return union.split("|").map((literal) => literal.trim().replace(/"/g, ""));
}

function bodyFor(url: string): unknown {
  if (url.includes("/inspection/patterns")) return PATTERNS;
  if (url.includes("/signals/trend")) return TREND;
  if (url.includes("/knowledge/")) return DOCUMENT;
  if (url.includes("/components/")) return COMPONENT;
  if (url.includes("/lots/")) return LOT;
  if (url.includes("/alarms/")) return ALARM;
  if (url.includes("/stops/")) return STOP;
  if (url.includes("/parts/")) return PART;
  throw new Error(`no fixture for ${url}`);
}

function stubEveryEndpoint(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: string) =>
    Promise.resolve(
      new Response(JSON.stringify(bodyFor(input)), {
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Renders the chip signed in, and opens it.
 *
 * Scoped to the tree this call rendered rather than to the document: one test below opens
 * every kind at once, and a document-wide query would keep finding — and re-toggling — the
 * first chip of the nine.
 */
function open(citation: Citation): void {
  const view = render(
    <AuthProvider>
      <CitationChip citation={citation} />
    </AuthProvider>,
  );
  fireEvent.click(
    within(view.container).getAllByRole("button")[0] as HTMLElement,
  );
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, TOKEN);
  // jsdom implements neither half of the object-URL API, and the `part` panel reaches for
  // it as soon as an inspected part carries an image.
  URL.createObjectURL = vi.fn(() => "blob:inspection-image");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test.each(declaredKinds())(
  "a %s citation opens onto a record",
  async (kind) => {
    // The kinds are enumerated from the contract, not typed out, so a tenth kind fails here
    // rather than quietly going untested. §7.3: adding a kind means adding a renderer.
    const citation = SAMPLES[kind as Citation["kind"]];
    expect(citation, `no sample citation for the kind ${kind}`).toBeDefined();
    stubEveryEndpoint();

    open(citation);

    const panel = await screen.findByTestId("evidence-panel");
    expect(panel).toHaveAttribute("data-outcome", "open");
  },
);

test.each(declaredKinds())(
  "a %s citation the service cannot find says the service looked",
  async (kind) => {
    // §6.5 rests on "no such id" and "nothing to show" being different facts. The
    // containment kind is the one that cannot 404 — its referent is the serials it carries
    // — so it opens onto its own scope and each serial inside it resolves separately.
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "no such thing here" }), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );

    open(SAMPLES[kind as Citation["kind"]]);

    const panel = await screen.findByTestId("evidence-panel");
    if (kind === "containment") {
      expect(panel).toHaveAttribute("data-outcome", "open");
      return;
    }
    expect(panel).toHaveAttribute("data-outcome", "not-found");
    expect(panel).toHaveTextContent("the service looked and found nothing");
    expect(panel).toHaveTextContent("no such thing here");
  },
);

test("a service that cannot be reached is a different message from a 404", async () => {
  // Different because a reader acts on them differently: a 404 is a citation the agent
  // should not have shipped, and an unreachable service says nothing about the citation.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
  );

  open(SAMPLES.stop);

  const panel = await screen.findByTestId("evidence-panel");
  expect(panel).toHaveAttribute("data-outcome", "failed");
  expect(panel).toHaveTextContent("could not be reached");
  expect(panel).not.toHaveTextContent("the service looked");
});

test("a SOP citation shows the procedure's own text", async () => {
  // §7.2 names this one explicitly: "including the SOP text itself". A title and an id
  // would be a citation that opens onto its own label.
  stubEveryEndpoint();

  open(SAMPLES.sop);

  expect(
    await screen.findByText(/Read the state timeline/),
  ).toBeInTheDocument();
  expect(screen.getByText("Line stop — first response")).toBeInTheDocument();
});

test("an alarm opens by id, with no window anywhere in the request", async () => {
  // The reason `GET /alarms/{id}` was added to the contract at all: a citation carries an
  // id and no interval, and resolving one out of `/alarms?from&to` would mean guessing the
  // window it was raised in.
  const fetchMock = stubEveryEndpoint();

  open(SAMPLES.alarm);

  expect(
    await screen.findByText("joining force out of tolerance"),
  ).toBeInTheDocument();
  const requested = String(fetchMock.mock.calls[0]?.[0]);
  expect(requested).toBe("/api/analysis/alarms/207");
});

test("every request a citation makes carries the reader's token", async () => {
  // §10.5. Asserted over every kind at once rather than one test per kind, because the
  // failure it guards against is one renderer built without the token, not all of them.
  const fetchMock = stubEveryEndpoint();

  for (const kind of declaredKinds()) {
    open(SAMPLES[kind as Citation["kind"]]);
  }
  await screen.findAllByTestId("evidence-panel");

  // At least one request per kind that has an endpoint. `containment` is the one that has
  // none of its own — its referent is the serials it carries — so it is one fewer than the
  // nine. Without this the loop below would pass loudest on an empty list of requests.
  expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(
    declaredKinds().length - 1,
  );
  for (const [, init] of fetchMock.mock.calls as [string, RequestInit][]) {
    expect(new Headers(init.headers).get("Authorization")).toBe(
      `Bearer ${TOKEN}`,
    );
  }
});

test("a stop citation shows the chain it was derived from, station states and all", async () => {
  // §5.4 returns the chain as data so that it can be disagreed with (§6.5), and a reader
  // can only disagree with reasoning that is on the screen.
  stubEveryEndpoint();

  open(SAMPLES.stop);

  expect(await screen.findByText("Propagation chain")).toBeInTheDocument();
  expect(screen.getByText("Suspended")).toBeInTheDocument();
  // Colour is never the only channel (ISA-101): the category is written out beside it.
  expect(screen.getByText("waiting on others")).toBeInTheDocument();
  expect(screen.getByText("held by own fault")).toBeInTheDocument();
  expect(screen.getByText(/starved:B3_4/)).toBeInTheDocument();
});

test("a pattern cell the report does not hold is neither an error nor an empty panel", async () => {
  // `/inspection/patterns` answers 200 with a whole report, and the cited cell may not be
  // in it. Rendering that as a blank would read as "no pattern here", which is a claim.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(PATTERNS), {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );

  open({ kind: "pattern", dimension: "carrier", key: "3" });

  const missing = await screen.findByTestId("citation-missing");
  expect(missing).toHaveTextContent("carrier=3");
  expect(missing).toHaveTextContent("none of them this one");
});

test("a windowed citation says which window it was opened over", async () => {
  // The contract carries no window on a citation (§7.3 specifies one), so the panel chooses
  // a recent one. A reader who took that for the answer's own window would have been told
  // something false by a screen that looked right.
  stubEveryEndpoint();

  open(SAMPLES.signal);

  expect(
    await screen.findByText(/carries no window of its own/),
  ).toBeInTheDocument();
});

test("a citation carrying nothing to resolve names that as the failure", async () => {
  // `agent.answer.Citation` refuses one, so this is the service having sent a citation it
  // should have rejected — which is worth saying, and is not the same thing as a 404.
  const fetchMock = stubEveryEndpoint();

  open({ kind: "stop", id: null });

  const panel = await screen.findByTestId("evidence-panel");
  expect(panel).toHaveAttribute("data-outcome", "unaddressed");
  expect(panel).toHaveTextContent(
    "none of the fields that would say what it refers to",
  );
  // And nothing was asked of the service, because there was nothing to ask about.
  expect(fetchMock).not.toHaveBeenCalled();
});

test("a containment scope opens each serial it names", async () => {
  // The set is what the claim was made over, and every part in it is a part someone may
  // have to go and look at.
  stubEveryEndpoint();

  open(SAMPLES.containment);

  const panel = await screen.findByTestId("evidence-panel");
  fireEvent.click(within(panel).getByRole("button", { name: "A-00000007" }));

  expect(await screen.findByText("not inspected")).toBeInTheDocument();
});
