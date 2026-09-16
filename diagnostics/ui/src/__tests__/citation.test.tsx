/** §7.2: a citation you cannot open is barely a citation. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

// Vite's `?raw` suffix (declared by the `vite/client` types already in tsconfig.app.json)
// reads the generated file as text rather than importing its erased-at-runtime types, so
// the check below runs against the union `json2ts` wrote, not a copy of it kept here.
import generatedAnswerSource from "../generated/answer.ts?raw";
import { CitationChip, RENDERERS } from "../CitationChip";
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

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(part: Part): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(part)))),
  );
}

test("clicking a citation opens the underlying row", async () => {
  stubFetch(PART);
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  fireEvent.click(screen.getByText("A-00000007"));

  await waitFor(() => {
    expect(screen.getByTestId("evidence-panel")).toBeInTheDocument();
  });
  expect(await screen.findByText("gap 0.91 · crack 0.04")).toBeInTheDocument();
  expect(screen.getByRole("img")).toHaveAttribute(
    "src",
    "/api/analysis/parts/A-00000007/image",
  );
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
  // the generated union itself rather than a copy of today's nine kinds, so a tenth kind
  // added to contracts/answer.schema.json fails here on `pnpm test` rather than only in
  // `tsc`'s far less specific "Record is missing these properties" error.
  const declaration = /export type Kind = (.+);/.exec(generatedAnswerSource);
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
