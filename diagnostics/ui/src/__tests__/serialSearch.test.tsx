/** §7.2's serial search, and the two kinds of serial it has to tell apart.
 *
 * A quality engineer holding a label reads an assembly serial; a supplier's recall email
 * names a component serial (§3.5 scenario 7). Nothing in either string says which it is, and
 * a search that knew only the first would answer "no such part" to the question this system
 * exists to answer in a minute rather than a morning.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type { ComponentAssembly, Part } from "../api";

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

function search() {
  render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/search"]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
}

function lookUp(serial: string): void {
  fireEvent.change(screen.getByLabelText("Serial"), {
    target: { value: serial },
  });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "dev-token-123");
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("a serial that names an assembly reaches the part view", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(json(PART))),
  );
  search();
  lookUp("A-00000007");

  const link = await screen.findByRole("link", {
    name: /Everything the line recorded/,
  });
  expect(link).toHaveAttribute("href", "/parts/A-00000007");
});

test("a serial no assembly carries is looked up as a component serial", async () => {
  // RULING M4-R5: the two kinds live at two endpoints, and the second one is the whole of
  // §3.5 scenario 7 — a supplier names one component and asks which assemblies carry it.
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) =>
      Promise.resolve(
        input.includes("/components/")
          ? json(COMPONENT)
          : json({ detail: "no part C-000123" }, 404),
      ),
    ),
  );
  search();
  lookUp("C-000123");

  expect(
    await screen.findByText(/looked up as a component serial/),
  ).toBeInTheDocument();
  // And it names the assembly it went into, which is the answer the recall wanted.
  expect(
    await screen.findByRole("button", { name: "A-00000007" }),
  ).toBeInTheDocument();
});

test("a serial of neither kind says both lookups looked", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(json({ detail: "no such serial" }, 404))),
  );
  search();
  lookUp("Z-000000");

  expect(
    await screen.findByText(/No assembly carries the serial/),
  ).toBeInTheDocument();
  expect(
    await screen.findByText(/the service looked and found nothing/),
  ).toBeInTheDocument();
});

test("a lookup that failed does not report the serial as unknown", async () => {
  // The difference §6.5 rests on, in the place it is easiest to lose: a service that did
  // not answer says nothing whatever about whether the line built this part, and reporting
  // it as "no such serial" would be a claim made on the evidence of a network failure.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
  );
  search();
  lookUp("A-00000007");

  expect(await screen.findByText(/Could not look up/)).toBeInTheDocument();
  expect(
    screen.queryByText(/No assembly carries the serial/),
  ).not.toBeInTheDocument();
});

test("nothing is looked up before a serial is given", () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  search();

  expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalled();
});
