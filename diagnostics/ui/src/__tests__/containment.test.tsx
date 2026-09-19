/** §7.2's containment view, and the export somebody acts on.
 *
 * *"The export is the set the query actually returned, and carries its own count — an export
 * that silently differs from what was on screen is worse than no export."* So the assertion
 * that matters below compares the file against the screen serial by serial, and against the
 * counts the service reported: a file short by one part is a part still in a customer's
 * hands, and nothing about the screen it came from would look wrong.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type { AffectedParts } from "../api";

const AFFECTED: AffectedParts = {
  window: { from_ts: "2026-09-12T01:00:00Z", to_ts: "2026-09-12T02:00:00Z" },
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

function stub(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
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

function ask(criteria: Record<string, string> = {}): void {
  fireEvent.change(screen.getByLabelText("From, UTC"), {
    target: { value: "2026-09-12T01:00:00Z" },
  });
  fireEvent.change(screen.getByLabelText("To, UTC"), {
    target: { value: "2026-09-12T02:00:00Z" },
  });
  for (const [label, value] of Object.entries(criteria)) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(
    screen.getByRole("button", { name: "Find the affected set" }),
  );
}

/** The export, read back out of the blob the link points at. */
async function exportedCsv(): Promise<string> {
  await screen.findByRole("link", { name: /Export this set/ });
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
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "dev-token-123");
  exported = [];
  URL.createObjectURL = vi.fn((blob: Blob) => {
    exported.push(blob);
    return `blob:export-${String(exported.length)}`;
  }) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("no window is searched until one is given", () => {
  const fetchMock = stub(AFFECTED);
  containment();

  expect(
    screen.getByRole("button", { name: "Find the affected set" }),
  ).toBeDisabled();
  // And the screen says why there is no default rather than silently having none: the
  // clock every analysis reads is the line's simulated one, not this browser's.
  expect(screen.getByText(/not pre-filled/)).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("the affected set is shown split by where each part went", async () => {
  // §5.3's split is the whole reason `/parts/affected` exists: the shipped ones are what
  // somebody has to act on tonight, and a total would bury them.
  stub(AFFECTED);
  containment();
  ask({ "Lot code": "L-4471" });

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
  stub(AFFECTED);
  containment();
  ask();

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
  expect(
    screen.getByRole("link", { name: /Export this set — 5 row\(s\)/ }),
  ).toHaveAttribute("download", "containment-5-of-5-parts.csv");
});

test("an export of a capped list says it is shorter than the count", async () => {
  // `count` is exact and `serials` is capped. A file holding one of 278 shipped parts is a
  // true answer; a file that let itself be read as all 278 is the failure that matters here.
  stub(CAPPED);
  containment();
  ask();

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
    ...AFFECTED,
    parts: {
      total: 0,
      rejected: { count: 0, serials: [], truncated: false },
      shipped: { count: 0, serials: [], truncated: false },
      on_the_line: { count: 0, serials: [], truncated: false },
    },
  });
  containment();
  ask();

  const csv = await exportedCsv();
  expect(serialsIn(csv)).toEqual([]);
  expect(csv).toContain("# scope: 0 assemblies");
  expect(csv).toContain("# in this file: 0 row(s)");
});

test("criteria the service refuses are reported in its own words", async () => {
  // §5.3 gives `/parts/affected` three different answers on purpose — 404 for a station the
  // line does not have, 422 for one that records no instant, an empty scope for criteria
  // that matched nothing — because they call for three different next steps (§6.5). A
  // reader told only "the request failed" retries a question that can never be answered in
  // that shape.
  stub({ detail: "station S2 records no instant against a part" }, 422);
  containment();
  ask({ Station: "S2" });

  expect(
    await screen.findByText(/records no instant against a part/),
  ).toBeInTheDocument();
});
