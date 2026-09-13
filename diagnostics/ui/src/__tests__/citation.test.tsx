/** §7.2: a citation you cannot open is barely a citation. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { CitationChip, RENDERERS } from "../CitationChip";
import type { Part } from "../api";

const PART: Part = {
  assembly_serial: "A-00000007",
  source_ts: "2026-09-12T13:41:07Z",
  station: "S3",
  result: "reject",
  defect_class: "gap",
  confidence: 0.91,
  model_version: "sim-1",
  image_url: "/parts/A-00000007/image",
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
  expect(await screen.findByText("gap")).toBeInTheDocument();
  expect(screen.getByRole("img")).toHaveAttribute(
    "src",
    "/api/analysis/parts/A-00000007/image",
  );
});

test("a part with no image opens anyway, without a broken one", async () => {
  // §3.4: a good part has no image, and that is not a missing value.
  stubFetch({ ...PART, result: "ok", defect_class: null, image_url: null });
  render(<CitationChip citation={{ kind: "part", id: "A-00000007" }} />);

  fireEvent.click(screen.getByText("A-00000007"));

  expect(await screen.findByTestId("evidence-panel")).toBeInTheDocument();
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

test("each citation kind has its own renderer", () => {
  // §7.3: adding a citation type means adding a renderer, nothing more.
  expect(Object.keys(RENDERERS)).toContain("part");
});
