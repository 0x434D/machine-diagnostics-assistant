/** §7.2's containment view, and the export somebody acts on.
 *
 * *"The export is the set the query actually returned, and carries its own count — an export
 * that silently differs from what was on screen is worse than no export."* So the assertion
 * that matters below compares the file against the screen serial by serial, and against the
 * counts the service reported: a file short by one part is a part still in a customer's
 * hands, and nothing about the screen it came from would look wrong.
 *
 * The second thing pinned here is where the *window* comes from. A default read off this
 * browser's clock would return rows, read as an answer and scope a containment to the wrong
 * interval, so the tests below run under a system clock deliberately set nowhere near the
 * service's and insist the form still opens on the service's window.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type {
  AffectedParts,
  Coverage,
  LineStatus,
  PlantStatus,
  TimeResolution,
} from "../api";

/** What `/time/resolve` makes of "this shift" — the phrase the form opens on. */
const RESOLUTION: TimeResolution = {
  expression: "this shift",
  window: { from_ts: "2026-09-12T04:00:00Z", to_ts: "2026-09-12T12:00:00Z" },
  label: "early shift 2026-09-12 06:00 – 2026-09-12 14:00 Europe/Berlin",
  closed: false,
  now: "2026-09-12T09:00:00Z",
};

/** A line whose simulated clock is **ahead** of the wall clock, which §5.3 reports as a
 * negative staleness rather than clamping it away. */
const LINE_STATUS: LineStatus = {
  as_of: "2026-09-12T09:00:00Z",
  latest_data_at: "2026-09-12T10:00:00Z",
  staleness_seconds: -3600,
  live: true,
  live_within_seconds: 120,
  stations: [],
  buffers: [],
  active_alarms: [],
  last_part_out: null,
};

/** A window ingest missed nothing in. */
const COMPLETE: Coverage = {
  window: RESOLUTION.window,
  gaps: [],
  covered_fraction: 1,
  fully_covered: true,
  observed: {
    events: 4200,
    from_ts: RESOLUTION.window.from_ts,
    to_ts: RESOLUTION.window.to_ts,
  },
};

/** The same window with twenty minutes of it unobserved — the case this screen was reopened
 * for. Every part made in that hole is missing from the scope below and nothing in the
 * counts differs. */
const HOLED: Coverage = {
  window: RESOLUTION.window,
  gaps: [
    {
      from_ts: "2026-09-12T06:00:00Z",
      to_ts: "2026-09-12T06:20:00Z",
      reason: "gateway offline",
    },
  ],
  covered_fraction: 0.958,
  fully_covered: false,
  observed: {
    events: 3900,
    from_ts: RESOLUTION.window.from_ts,
    to_ts: RESOLUTION.window.to_ts,
  },
};

/** What the shell's plant status banner reads. It stands over every view, so a stub that
 * handed it a containment scope would render this view's own sentences under a heading about
 * the plant. */
const PLANT_STATUS: PlantStatus = {
  state: "live",
  lastEventSourceTs: "2026-09-12T10:00:00Z",
  backfillProgress: 1,
  queueDepth: 0,
  overflowCount: 0,
  clockAvailable: true,
};

const AFFECTED: AffectedParts = {
  window: RESOLUTION.window,
  criteria: {
    station: null,
    carrier: null,
    lot_code: "L-4471",
    defect_class: null,
    signal: null,
    below: null,
    above: null,
    anchor: "created",
    window_selects: "when the part was created",
  },
  parts: {
    total: 5,
    rejected: {
      count: 2,
      serials: ["A-00000007", "A-00000008"],
      truncated: false,
    },
    shipped: {
      count: 2,
      serials: ["A-00000009", "A-00000010"],
      truncated: false,
    },
    on_the_line: { count: 1, serials: ["A-00000011"], truncated: false },
  },
  unplaceable: 0,
};

/** The same scope, with the service's cap hit on the shipped list. `count` stays exact. */
const CAPPED: AffectedParts = {
  ...AFFECTED,
  parts: {
    ...AFFECTED.parts,
    total: 340,
    shipped: { count: 278, serials: ["A-00000009"], truncated: true },
  },
  unplaceable: 12,
};

/** Every blob the view handed to `createObjectURL`, in order. */
let exported: Blob[] = [];

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The endpoints this screen reads, answered by URL.
 *
 * One mock returning one body for every request would let a test pass with the window read
 * from the wrong response, which is the failure this file exists to catch.
 */
function stub(
  options: {
    affected?: unknown;
    affectedStatus?: number;
    coverage?: Coverage;
    /** The `/coverage` request failing is its own case: the scope is still answerable and
     * whether it is complete is not. */
    coverageStatus?: number;
    /** Expressions `/time/resolve` refuses, as the real endpoint does — with the list of
     * the ones it understands, which is the half a reader can act on. */
    refuses?: string[];
  } = {},
): ReturnType<typeof vi.fn> {
  const refuses = options.refuses ?? [];
  const fetchMock = vi.fn((input: unknown) => {
    const url = String(input);
    if (url.startsWith("/api/gateway")) {
      return Promise.resolve(json(PLANT_STATUS, 200));
    }
    if (url.includes("/time/resolve")) {
      const expression =
        new URL(url, "http://localhost").searchParams.get("expression") ?? "";
      return Promise.resolve(
        refuses.includes(expression)
          ? json(
              {
                detail: {
                  message: `no understood time expression matches '${expression}'`,
                  understood: ["last night", "last shift", "this shift"],
                },
              },
              422,
            )
          : json({ ...RESOLUTION, expression }, 200),
      );
    }
    if (url.includes("/line/status")) {
      return Promise.resolve(json(LINE_STATUS, 200));
    }
    if (url.includes("/coverage")) {
      return Promise.resolve(
        json(options.coverage ?? COMPLETE, options.coverageStatus ?? 200),
      );
    }
    return Promise.resolve(
      json(options.affected ?? AFFECTED, options.affectedStatus ?? 200),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function containment() {
  render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/containment"]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
}

/** Which URLs the view fetched, in order. */
function requested(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls.map((call) => String(call[0]));
}

/** Wait for the service's window to reach the form, then ask over it. */
async function ask(criteria: Record<string, string> = {}): Promise<void> {
  await screen.findByDisplayValue(RESOLUTION.window.from_ts);
  for (const [label, value] of Object.entries(criteria)) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(
    screen.getByRole("button", { name: "Find the affected set" }),
  );
}

/** The export, read back out of the blob the link points at.
 *
 * Waits for the window's coverage as well as for the scope. The two come back from separate
 * requests and the file is written from both, so reading the blob before the coverage
 * settles is reading a file nobody ever downloads.
 */
async function exportedCsv(): Promise<string> {
  await screen.findByRole("link", { name: /Export this set/ });
  await waitFor(() => {
    expect(screen.getByTestId("scope-coverage")).not.toHaveAttribute(
      "data-outcome",
      "opening",
    );
  });
  const blob = exported.at(-1);
  if (blob === undefined) throw new Error("the view exported nothing");
  return await blob.text();
}

/** The serials the export holds, in the order it holds them. */
function serialsIn(csv: string): string[] {
  const lines = csv.split("\n");
  const header = lines.indexOf("assembly_serial,outcome");
  expect(header).toBeGreaterThan(-1);
  return lines
    .slice(header + 1)
    .filter((line) => line !== "")
    .map((line) => line.split(",")[0] ?? "");
}

beforeEach(() => {
  // Only `Date` is faked. Faking the timers as well would stop `findBy*` resolving, and the
  // clock is the only thing these tests need to lie about: every window below must come
  // from a service even though this browser believes it is a different month entirely.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-01T23:17:00Z"));
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "dev-token-123");
  exported = [];
  URL.createObjectURL = vi.fn((blob: Blob) => {
    exported.push(blob);
    return `blob:export-${String(exported.length)}`;
  }) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("the default window is the service's and not this browser's clock", async () => {
  // The finding this view was reopened for: an operator had to know an instant before they
  // could ask the question. The fix must not be the other failure — a "last 24 hours" off
  // `Date.now()`, which looks sensible and answers over the wrong interval, because all
  // analysis reads the line's simulated clock rather than this device's.
  const fetchMock = stub();
  containment();

  expect(
    await screen.findByDisplayValue(RESOLUTION.window.from_ts),
  ).toBeInTheDocument();
  expect(screen.getByDisplayValue(RESOLUTION.window.to_ts)).toBeInTheDocument();

  // The system clock here says November. Nothing on the screen does.
  expect(new Date().getUTCMonth()).toBe(10);
  expect(screen.queryByDisplayValue(/2026-11/)).toBeNull();

  expect(
    requested(fetchMock).some((url) =>
      url.includes("/time/resolve?expression=this+shift"),
    ),
  ).toBe(true);
});

test("the window in force says which interval it is, and that it is still open", async () => {
  // §7.2's containment set is acted on away from this screen, so its interval has to be
  // readable without opening the form. And a window still being written to has to say so:
  // the same question asked an hour later answers over more parts.
  stub();
  containment();

  await screen.findByDisplayValue(RESOLUTION.window.from_ts);
  const inForce = screen.getByTestId("window-in-force");
  expect(inForce).toHaveTextContent(RESOLUTION.window.from_ts);
  expect(inForce).toHaveTextContent(RESOLUTION.window.to_ts);
  expect(inForce).toHaveTextContent("early shift");
  expect(inForce).toHaveTextContent(/still being written to at 2026-09-12/);
});

test("where the line's own clock has reached is on screen beside the window", async () => {
  // §5.3 allows simulated time to sit *ahead* of the wall clock and reports the negative
  // staleness rather than clamping it. A screen that showed "-3600 s" or hid the sign would
  // leave a reader assuming the calendar and the data agree.
  stub();
  containment();

  const clock = await screen.findByTestId("line-clock");
  expect(clock).toHaveTextContent("2026-09-12T10:00:00Z");
  expect(clock).toHaveTextContent(/3600 s ahead of/);
  expect(clock).toHaveTextContent(/simulated time is running ahead/);
});

test("nothing is searched until the question is asked", async () => {
  // The default is for the *form*. A containment query that ran on arrival would put a set
  // of parts on screen that nobody asked about, under a window nobody had read yet.
  const fetchMock = stub();
  containment();

  await screen.findByDisplayValue(RESOLUTION.window.from_ts);
  expect(
    requested(fetchMock).some((url) => url.includes("/parts/affected")),
  ).toBe(false);
  expect(
    screen.getByText(/Nothing is read until the question is asked/),
  ).toBeInTheDocument();
});

test("a refused phrase says what would have been accepted, and keeps the window", async () => {
  // **Never a guess.** `/time/resolve` answers an expression it does not understand with the
  // list of ones it does, precisely so the caller can try something real; collapsing that
  // into "422" would throw away the half of the refusal a reader needs. And the refusal must
  // not empty the window they already had.
  stub({ refuses: ["penultimate"] });
  containment();
  await screen.findByDisplayValue(RESOLUTION.window.from_ts);

  fireEvent.change(
    screen.getByLabelText("Another phrase the calendar understands"),
    { target: { value: "penultimate" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Resolve" }));

  const refusal = await screen.findByTestId("shift-refusal");
  expect(refusal).toHaveTextContent("penultimate");
  expect(refusal).toHaveTextContent("last night");
  expect(
    screen.getByDisplayValue(RESOLUTION.window.from_ts),
  ).toBeInTheDocument();
});

test("a window typed by hand is not given the calendar's name for another one", async () => {
  // The label is a claim about *which* window. Left standing over instants somebody has
  // since edited it would name an interval nothing on the screen is asking about.
  stub();
  containment();
  await screen.findByDisplayValue(RESOLUTION.window.from_ts);
  expect(screen.getByRole("button", { name: "this shift" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  fireEvent.change(screen.getByLabelText("From, UTC"), {
    target: { value: "2026-09-12T05:00:00Z" },
  });

  const inForce = screen.getByTestId("window-in-force");
  expect(inForce).not.toHaveTextContent("early shift");
  expect(inForce).toHaveTextContent(/typed by hand/);
  // And the chip goes with it: a phrase left marked would say the form is asking about the
  // shift when it is asking about something else.
  expect(screen.getByRole("button", { name: "this shift" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});

test("the affected set is shown split by where each part went", async () => {
  // §5.3's split is the whole reason `/parts/affected` exists: the shipped ones are what
  // somebody has to act on tonight, and a total would bury them.
  stub();
  containment();
  await ask({ "Lot code": "L-4471" });

  expect(await screen.findByText(/5 assemblies in all/)).toBeInTheDocument();
  expect(screen.getByText(/Rejected/)).toBeInTheDocument();
  expect(screen.getByText(/Shipped/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "A-00000009" }),
  ).toBeInTheDocument();
  // The criteria as the *service* echoed them, not as this form remembers sending them.
  expect(screen.getByText(/lot L-4471/)).toBeInTheDocument();
});

test("the export contains exactly the rows the view showed, and its count matches", async () => {
  stub();
  containment();
  await ask();

  const csv = await exportedCsv();
  const inFile = serialsIn(csv);

  // Every serial in the file is one the reader can see, and every serial the reader can
  // see is in the file. Either direction failing is a list somebody acts on that differs
  // from the one they checked.
  const onScreen = [
    ...AFFECTED.parts.rejected.serials,
    ...AFFECTED.parts.shipped.serials,
    ...AFFECTED.parts.on_the_line.serials,
  ];
  for (const serial of onScreen) {
    expect(screen.getByRole("button", { name: serial })).toBeInTheDocument();
  }
  expect(inFile).toEqual(onScreen);

  // And it carries its own count, in the file and in the name of the file.
  expect(csv).toContain("# scope: 5 assemblies");
  expect(csv).toContain("# in this file: 5 row(s)");
  // Above the count, because it is what the count can possibly be: the file states what
  // ingest recorded of the window, even when it recorded all of it.
  expect(csv).toContain("# ingest coverage: complete");
  expect(
    screen.getByRole("link", { name: /Export this set — 5 row\(s\)/ }),
  ).toHaveAttribute("download", "containment-5-of-5-parts.csv");
});

test("the exported file names the shift it was taken over", async () => {
  // The file is the copy acted on away from the screen that produced it, and "the early
  // shift" is how the person holding it will describe its interval to the next person.
  stub();
  containment();
  await ask();

  expect(await exportedCsv()).toContain(
    "# window: 2026-09-12T04:00:00Z to 2026-09-12T12:00:00Z — early shift",
  );
});

test("an export of a capped list says it is shorter than the count", async () => {
  // `count` is exact and `serials` is capped. A file holding one of 278 shipped parts is a
  // true answer; a file that let itself be read as all 278 is the failure that matters here.
  stub({ affected: CAPPED });
  containment();
  await ask();

  const csv = await exportedCsv();
  expect(serialsIn(csv)).toHaveLength(4);
  expect(csv).toContain("shipped 1 of 278 (the service capped this list)");
  expect(csv).toContain("# scope: 340 assemblies");
  expect(
    screen.getByRole("link", { name: /Export this set — 4 row\(s\)/ }),
  ).toHaveAttribute("download", "containment-4-of-340-parts.csv");

  // The parts that matched every criterion and had no instant to place them by are in the
  // file too: a containment list silently short is worse than one that says it is.
  expect(csv).toContain("12 part(s) matched the criteria");
  expect(
    await screen.findByText(/12 part\(s\) match the criteria/),
  ).toBeInTheDocument();
});

test("an empty scope exports a file that says it is empty", async () => {
  // An empty scope is a real answer — criteria that matched nothing — and it is the case
  // where a bare column header would read as a broken export rather than as a result.
  stub({
    affected: {
      ...AFFECTED,
      parts: {
        total: 0,
        rejected: { count: 0, serials: [], truncated: false },
        shipped: { count: 0, serials: [], truncated: false },
        on_the_line: { count: 0, serials: [], truncated: false },
      },
    },
  });
  containment();
  await ask();

  const csv = await exportedCsv();
  expect(serialsIn(csv)).toEqual([]);
  expect(csv).toContain("# scope: 0 assemblies");
  expect(csv).toContain("# in this file: 0 row(s)");
});

test("a containment set over a holed window says so beside the count and in the file", async () => {
  // The finding this closes. `/parts/affected` answers over the rows ingest happened to
  // record and says nothing about the ones it did not, so a window the gateway was down for
  // produces a shorter list and an identical-looking screen — and the thing the operator
  // then pulls parts from is the file.
  const fetchMock = stub({ coverage: HOLED });
  containment();
  await ask();

  // Beside the count, not under it: which twenty minutes are unobserved, in the service's
  // own words for why.
  expect(await screen.findByText(/1 ingest gap\(s\)/)).toBeInTheDocument();
  expect(screen.getByText(/gateway offline/)).toBeInTheDocument();

  // Over the window the service applied, and asked of `/coverage` rather than worked out
  // here from the line's clock.
  const asked = requested(fetchMock).map((url) => decodeURIComponent(url));
  expect(
    asked.some(
      (url) =>
        url.startsWith("/api/analysis/coverage?") &&
        url.includes(RESOLUTION.window.from_ts) &&
        url.includes(RESOLUTION.window.to_ts),
    ),
  ).toBe(true);

  const csv = await exportedCsv();
  expect(csv).toContain("# ingest coverage: 95.8 % covered, 1 ingest gap(s)");
  expect(csv).toContain(
    "# ingest gap: 2026-09-12T06:00:00Z to 2026-09-12T06:20:00Z — gateway offline",
  );
});

test("coverage that could not be read says the set may be short, not that it is whole", async () => {
  // The state that must not read as "complete". A screen silent about coverage because the
  // question failed is indistinguishable from one over a window with nothing missing, which
  // is the pair §4.4 exists to keep apart — and the file has to carry the same doubt,
  // because it is read further from here than anything else this view produces.
  stub({ coverageStatus: 503 });
  containment();
  await ask();

  await waitFor(() => {
    expect(screen.getByTestId("scope-coverage")).toHaveAttribute(
      "data-outcome",
      "failed",
    );
  });
  const said = screen.getByTestId("scope-coverage");
  expect(said).toHaveTextContent(/is not known/);
  expect(said).toHaveTextContent(/nothing on this screen can say/);
  // The scope itself came back and is still on screen: a coverage that failed is not a
  // containment that failed.
  expect(
    screen.getByRole("button", { name: "A-00000007" }),
  ).toBeInTheDocument();

  expect(await exportedCsv()).toContain("# ingest coverage: not known");
});

test("criteria the service refuses are reported in its own words", async () => {
  // §5.3 gives `/parts/affected` three different answers on purpose — 404 for a station the
  // line does not have, 422 for one that records no instant, an empty scope for criteria
  // that matched nothing — because they call for three different next steps (§6.5). A
  // reader told only "the request failed" retries a question that can never be answered in
  // that shape.
  stub({
    affected: { detail: "station S2 records no instant against a part" },
    affectedStatus: 422,
  });
  containment();
  await ask({ Station: "S2" });

  expect(
    await screen.findByText(/records no instant against a part/),
  ).toBeInTheDocument();
});
