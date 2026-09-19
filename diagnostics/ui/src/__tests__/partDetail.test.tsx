/** §7.2's part view, and the absences that are not failures.
 *
 * What is asserted is what a reader sees: that a serial in the address resolves to the whole
 * of §14's trace, that a part with no image, no genealogy or no verdict is rendered as the
 * ordinary part it is, and that the image behind a reject never goes out without the
 * reader's identity on it — the bug M5 had to fix once already.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type { Part } from "../api";

const TOKEN = "dev-token-123";

/** A whole part: built from two components, pressed, inspected, rejected and scrapped. */
const PART: Part = {
  assembly_serial: "A-00000007",
  created_at: "2026-09-12T01:30:01Z",
  carrier_id: 4,
  genealogy: [
    {
      component_serial: "C-000123",
      position: 1,
      lane: 2,
      read_at: "2026-09-12T01:29:55Z",
      lot_code: "L-4471",
      supplier: "Nordwerk",
    },
    // Everything but the serial and the position is unknown: a component read before the
    // gateway's history horizon. It is in the assembly and must be shown as one.
    {
      component_serial: "C-000124",
      position: 2,
      lane: null,
      read_at: null,
      lot_code: null,
      supplier: null,
    },
  ],
  process_values: [
    { station: "S2", signal: "PeakForce", value: 902.4 },
    { station: "S2", signal: "FinalPosition", value: 12.1 },
  ],
  process_curves: [
    { station: "S2", signal: "ForceDistance", samples: [1, 2, 3, 4] },
  ],
  inspection: {
    source_ts: "2026-09-12T01:30:09Z",
    station: "S3",
    result: "reject",
    defect_classes: ["gap", "crack"],
    confidences: [0.91, 0.04],
    confidence: 0.91,
    model_version: "sim-1",
    image_url: "/parts/A-00000007/image",
  },
  disposition: {
    at: "2026-09-12T01:30:20Z",
    disposition: "scrap",
    reason: "reject at S3",
  },
};

const IMAGE_BYTES = new Uint8Array([137, 80, 78, 71]);

function stub(part: Part): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: string) =>
    Promise.resolve(
      input.endsWith("/image")
        ? new Response(IMAGE_BYTES, {
            headers: { "Content-Type": "image/png" },
          })
        : new Response(JSON.stringify(part), {
            headers: { "Content-Type": "application/json" },
          }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function open(serial: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[`/parts/${serial}`]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
}

beforeEach(() => {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, TOKEN);
  // jsdom implements neither half of the object-URL API, and the view reaches for it as
  // soon as an inspected part carries an image.
  URL.createObjectURL = vi.fn(() => "blob:inspection-image");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("a serial resolves to its part and its genealogy", async () => {
  stub(PART);
  open("A-00000007");

  // The assembly, and both of the components it was built from — including the one whose
  // own origin is unknown, which must not be dropped from the list a containment is built
  // on (§3.4a).
  expect(await screen.findByText("A-00000007")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "C-000123" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "C-000124" })).toBeInTheDocument();
  expect(screen.getByText(/L-4471/)).toBeInTheDocument();
  expect(screen.getByText(/lot unknown/)).toBeInTheDocument();
});

test("the process values recorded for that part are shown against the station that recorded them", async () => {
  stub(PART);
  open("A-00000007");

  const press = await screen.findByRole("heading", { name: "S2" });
  const step = press.closest("li");
  expect(step).not.toBeNull();
  const shown = within(step as HTMLElement);
  expect(shown.getByText(/PeakForce 902.4/)).toBeInTheDocument();
  expect(shown.getByText(/FinalPosition 12.1/)).toBeInTheDocument();
  expect(shown.getByText(/ForceDistance: 4 samples/)).toBeInTheDocument();
});

test("a station whose record carries no instant says so rather than borrowing one", async () => {
  // §3.4a: the press records two numbers against the serial and no time beside them. A
  // plausible instant here is the inference that makes a containment list unusable, so the
  // screen has to be able to say it does not have one.
  stub(PART);
  open("A-00000007");

  const press = await screen.findByRole("heading", { name: "S2" });
  expect(
    within(press.closest("li") as HTMLElement).getByText("no instant recorded"),
  ).toBeInTheDocument();

  // And the verdict, which does carry one, shows it.
  const camera = screen.getByRole("heading", { name: "S3" });
  expect(
    within(camera.closest("li") as HTMLElement).getByText(
      "2026-09-12T01:30:09Z",
    ),
  ).toBeInTheDocument();
});

test("a part missing an image renders the absence rather than a broken image", async () => {
  // §3.4: a good part has no image, and that is not a missing value.
  stub({
    ...PART,
    inspection: {
      ...PART.inspection,
      result: "good",
      image_url: null,
    } as Part["inspection"],
  });
  open("A-00000007");

  expect(await screen.findByText("good")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Could not open the inspection image/),
  ).not.toBeInTheDocument();
});

test("an image the service refuses says so rather than leaving a gap", async () => {
  // A blank where the image should be reads as "this part has no image", which is the good
  // part above and a different fact from the one that happened.
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) =>
      Promise.resolve(
        input.endsWith("/image")
          ? new Response(
              JSON.stringify({ detail: "authentication required" }),
              {
                status: 401,
                headers: { "Content-Type": "application/json" },
              },
            )
          : new Response(JSON.stringify(PART), {
              headers: { "Content-Type": "application/json" },
            }),
      ),
    ),
  );
  open("A-00000007");

  expect(
    await screen.findByText(/Could not open the inspection image/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("the inspection image is fetched with the reader's token", async () => {
  // The bug M5 fixed once: a browser cannot put an `Authorization` header on an `<img>`,
  // and that endpoint refuses an unauthenticated request. The view must not hand the URL
  // back to the browser.
  const fetchMock = stub(PART);
  open("A-00000007");

  expect(
    await screen.findByAltText("Inspection image for A-00000007"),
  ).toBeInTheDocument();
  const requests = fetchMock.mock.calls.filter(([input]) =>
    String(input).endsWith("/image"),
  ) as [string, RequestInit | undefined][];
  expect(requests.length).toBeGreaterThan(0);
  for (const [, init] of requests) {
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      `Bearer ${TOKEN}`,
    );
  }
});

test("a part with no parents, no verdict and no values is a part rather than a failure", async () => {
  // Every part between the press and the camera is in some of this state, and an assembly
  // created before the gateway's history horizon is in all of it.
  stub({
    assembly_serial: "A-00000008",
    created_at: null,
    carrier_id: null,
    genealogy: [],
    process_values: [],
    process_curves: [],
    inspection: null,
    disposition: null,
  });
  open("A-00000008");

  expect(
    await screen.findByText(/No components are recorded/),
  ).toBeInTheDocument();
  expect(screen.getByText("not inspected")).toBeInTheDocument();
  expect(screen.getByText("still on the line")).toBeInTheDocument();
  // Not an error panel: nothing here failed.
  expect(screen.getByTestId("evidence-panel")).toHaveAttribute(
    "data-outcome",
    "open",
  );
});

test("a serial the service has never seen says the service looked", async () => {
  // §6.5: "no such serial" and "nothing to show" are different facts, and a part view is
  // exactly where a reader acts on the difference.
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "no part A-99999999" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
  open("A-99999999");

  expect(
    await screen.findByText(/the service looked and found nothing/),
  ).toBeInTheDocument();
});
