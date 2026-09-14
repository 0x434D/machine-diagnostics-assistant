import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PartStrip } from "../PartStrip";
import type { Disposition, PartView } from "../snapshot";
// Vite's ?raw, not node:fs: the same resolver the bundle is built with, so the file
// asserted here is the file that ships.
import sheet from "../styles.css?raw";

/** The two verdicts `inspection.classifier` returns, mirroring `snapshot.Disposition`. */
const DISPOSITIONS: Disposition[] = ["good", "reject"];

const REJECT: PartView = {
  serial: "A-00000412",
  at: "2026-09-13T06:09:54+00:00",
  disposition: "reject",
  reason: "gap",
  image_url: "/parts/A-00000412/image",
};

const GOOD: PartView = {
  serial: "A-00000411",
  at: "2026-09-13T06:09:48+00:00",
  disposition: "good",
  reason: "",
  image_url: null,
};

describe("the part strip", () => {
  it("names every part by the serial M2b gave it", () => {
    // The point of the strip on a demo console: a serial read off this screen is the
    // same string the diagnostics stack answers /parts/{serial} for.
    render(<PartStrip parts={[REJECT, GOOD]} />);

    expect(screen.getByText("A-00000412")).toBeInTheDocument();
    expect(screen.getByText("A-00000411")).toBeInTheDocument();
  });

  it("keeps the newest part first, as the payload orders them", () => {
    render(<PartStrip parts={[REJECT, GOOD]} />);

    const serials = screen
      .getAllByTestId("part")
      .map((part) => part.querySelector(".part__serial")?.textContent);
    expect(serials).toEqual(["A-00000412", "A-00000411"]);
  });

  it("shows a reject's image and the reason it was rejected", () => {
    render(<PartStrip parts={[REJECT, GOOD]} />);

    // §3.7's thumbnail. Loaded from the simulator through the page's own origin, which
    // is what the /api/plant prefix is — the payload carries the plant-side path only.
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      expect.stringContaining("/api/plant/parts/A-00000412/image"),
    );
    expect(screen.getByText("gap")).toBeInTheDocument();
  });

  it("gives a good part no image rather than a broken one", () => {
    // §3.4: only rejects carry an image, and that absence is a fact about the part.
    render(<PartStrip parts={[GOOD]} />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("says there are no parts rather than rendering an empty strip", () => {
    // Before the first part is inspected there genuinely are none, and blank space
    // reads as a strip that failed to load.
    render(<PartStrip parts={[]} />);

    expect(screen.getByText(/no parts inspected yet/)).toBeInTheDocument();
  });

  it("colours by disposition and names it as text beside the colour", () => {
    // ISA-101, the same rule the station tiles follow: the class is the colour and the
    // word is the second channel, and asserting both together is what stops one of them
    // being dropped later as redundant.
    render(<PartStrip parts={[REJECT, GOOD]} />);

    const [reject, good] = screen.getAllByTestId("part");
    expect(reject).toHaveClass("part--reject");
    expect(reject).toHaveTextContent("reject");
    expect(good).toHaveClass("part--good");
    expect(good).toHaveTextContent("good");
  });

  it("gives every disposition a rule to render with", () => {
    // jsdom applies no stylesheet, so the class assertion above cannot see whether the
    // class resolves to anything. A disposition with no rule renders as
    // --no-such-disposition rather than as a plausible neighbour, which is the same
    // trap the five station categories carry a rule each against.
    for (const disposition of DISPOSITIONS) {
      expect(sheet).toContain(`.part--${disposition} {`);
      expect(sheet).toContain(`--${disposition}:`);
    }
  });
});
