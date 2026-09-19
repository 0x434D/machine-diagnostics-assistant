/** §7.2: a citation you cannot open is barely a citation. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// Vite's `?raw` suffix (declared by the `vite/client` types already in tsconfig.app.json)
// reads the generated file as text rather than importing its erased-at-runtime types, so
// the check below runs against the union `json2ts` wrote, not a copy of it kept here.
import generatedAnswerSource from "../generated/answer.ts?raw";
import { AuthProvider } from "../AuthContext";
import { CitationChip, RENDERERS } from "../CitationChip";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";
import type { Part } from "../api";

const INSPECTION: NonNullable<Part["inspection"]> = {
  source_ts: "2026-09-12T13:41:07Z",
  station: "S3",
  result: "reject",
  // §3.4's parallel arrays over every class, on good parts too. The panel shows all of
  // them rather than the one that won: two can be high at once.
  defect_classes: ["gap", "crack"],
  confidences: [0.91, 0.04],
  confidence: 0.91,
  model_version: "sim-1",
  image_url: "/parts/A-00000007/image",
};

const PART: Part = {
  assembly_serial: "A-00000007",
  created_at: "2026-09-12T13:41:01Z",
  carrier_id: 4,
  genealogy: [],
  process_values: [],
  process_curves: [],
  inspection: INSPECTION,
  disposition: null,
};

/** The first four bytes of a PNG. Nothing here reads them; what matters is that the panel
 * had to fetch something rather than hand the endpoint to the browser. */
const IMAGE_BYTES = new Uint8Array([137, 80, 78, 71]);

beforeEach(() => {
  // jsdom implements neither half of the object-URL API, so the panel's image path cannot
  // run at all without these. They are assigned rather than `vi.stubGlobal`-ed because they
  // are properties of `URL`, not globals of their own; `clearMocks` resets the calls between
  // tests and `beforeEach` re-installs them.
  URL.createObjectURL = vi.fn(() => "blob:inspection-image");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function stubFetch(part: Part): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((input: string) =>
    Promise.resolve(
      input.endsWith("/image")
        ? new Response(IMAGE_BYTES, {
            headers: { "Content-Type": "image/png" },
          })
        : new Response(JSON.stringify(part)),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Every request the panel made for the reject image. */
function imageRequests(
  fetchMock: ReturnType<typeof vi.fn>,
): [string, RequestInit | undefined][] {
  return fetchMock.mock.calls.filter(([input]) =>
    String(input).endsWith("/image"),
  ) as [string, RequestInit | undefined][];
}

test("clicking a citation opens the underlying row", async () => {
  stubFetch(PART);
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  fireEvent.click(screen.getByText("A-00000007"));

  await waitFor(() => {
    expect(screen.getByTestId("evidence-panel")).toBeInTheDocument();
  });
  expect(await screen.findByText("gap 0.91 · crack 0.04")).toBeInTheDocument();
  // The reject image is shown. Deliberately *not* asserted by its `src`: that endpoint is
  // auth-gated, so how the bytes reach the element is a decision this test has no business
  // pinning — what §7.2 asks is that the evidence is in front of the reader.
  expect(
    await screen.findByAltText("Inspection image for A-00000007"),
  ).toBeInTheDocument();
});

test("the reject image is fetched with the reader's token", async () => {
  // The bug this replaces: `<img src={endpoint}>` is a request the browser makes on its
  // own, and a browser cannot put an `Authorization` header on an `<img>`. Since M5 that
  // endpoint refuses an unauthenticated request, so the evidence a quality engineer opened
  // the citation for silently failed to load. The assertion is that no request for those
  // bytes goes out without the reader's identity on it.
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "dev-token-123");
  const fetchMock = stubFetch(PART);
  render(
    <AuthProvider>
      <CitationChip citation={{ kind: "part", id: "A-00000007" }} />
    </AuthProvider>,
  );

  fireEvent.click(screen.getByText("A-00000007"));

  expect(
    await screen.findByAltText("Inspection image for A-00000007"),
  ).toBeInTheDocument();
  const requests = imageRequests(fetchMock);
  expect(requests.length).toBeGreaterThan(0);
  for (const [, init] of requests) {
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer dev-token-123",
    );
  }
});

test("an image the server refuses says so rather than leaving a gap", async () => {
  // A blank where the image should be reads as "this part has no image", which is §3.4's
  // good part and a different fact from the one that happened.
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
          : new Response(JSON.stringify(PART)),
      ),
    ),
  );
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  fireEvent.click(screen.getByText("A-00000007"));

  expect(
    await screen.findByText(/Could not open the inspection image/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("closing a citation releases the image it was holding", async () => {
  // Tied to the object-URL route rather than to §7.2, and says so: a blob URL that outlives
  // its element pins the bytes for the life of the document, and a reader clicking through
  // citations for an afternoon creates one per click. If the bytes ever arrive another way,
  // this test goes with the mechanism it is about.
  stubFetch(PART);
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  // By role, because once the panel is open the serial appears twice on the page — on the
  // chip and in the row it opened.
  const chip = screen.getByRole("button", { name: "A-00000007" });
  fireEvent.click(chip);
  await screen.findByAltText("Inspection image for A-00000007");
  fireEvent.click(chip);

  await waitFor(() => {
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:inspection-image");
  });
});

test("a part with no image opens anyway, without a broken one", async () => {
  // §3.4: a good part has no image, and that is not a missing value.
  stubFetch({
    ...PART,
    inspection: { ...INSPECTION, result: "good", image_url: null },
  });
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  fireEvent.click(screen.getByText("A-00000007"));

  expect(await screen.findByTestId("evidence-panel")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("a part that has not reached the camera opens with what is known", async () => {
  // Not a third failure mode: every serial between the press and the camera is in this
  // state, and so is every part created before the gateway's history horizon. §14's
  // trace answers for them rather than omitting them.
  stubFetch({ ...PART, created_at: null, inspection: null });
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  fireEvent.click(screen.getByText("A-00000007"));

  expect(await screen.findByTestId("evidence-panel")).toBeInTheDocument();
  expect(screen.getByText("unknown")).toBeInTheDocument();
  expect(screen.getByText("not inspected")).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("a citation that does not resolve says so rather than showing nothing", async () => {
  // §6.5 depends on the difference between "no such id" and "nothing to show".
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response("null", { status: 404, statusText: "Not Found" }),
      ),
    ),
  );
  render(<CitationChip citation={{ kind: "part", id: "A-99999999" }} />);

  fireEvent.click(screen.getByText("A-99999999"));

  expect(
    await screen.findByText(/Could not open A-99999999/),
  ).toBeInTheDocument();
});

test("a renderer exists for every kind the contract declares", () => {
  // §7.3: adding a citation type means adding a renderer, nothing more — checked against
  // the generated union itself rather than a copy of today's ten kinds, so an eleventh kind
  // added to contracts/answer.schema.json fails here on `pnpm test` rather than only in
  // `tsc`'s far less specific "Record is missing these properties" error.
  //
  // Across lines, because `json2ts` wraps a union it cannot fit on one and did so the day
  // `chart` was added — at which point a single-line pattern stops matching and the test
  // that guards the seam starts guarding nothing.
  const declaration = /export type Kind =([\s\S]+?);/.exec(
    generatedAnswerSource,
  );
  const union = declaration?.[1];
  if (union === undefined) {
    throw new Error("generated/answer.ts has no `Kind` union to check against");
  }
  const kinds = union
    .split("|")
    .map((literal) => literal.trim().replace(/"/g, ""));

  expect(kinds.length).toBeGreaterThan(0);
  for (const kind of kinds) {
    expect(Object.keys(RENDERERS)).toContain(kind);
  }
});
