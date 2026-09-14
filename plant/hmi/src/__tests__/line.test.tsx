import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Line } from "../Line";
import type { LineSnapshot } from "../snapshot";

/** S2 held with a jam, S3 starved behind it — the exact situation the categories exist
 * to tell apart, and the one Task 12 drives on the real line. */
const HELD_S2: LineSnapshot = {
  phase: "live",
  simulated_now: "2026-09-13T06:10:00+00:00",
  history_start: "2026-09-13T05:00:00+00:00",
  catchup_speed: 700,
  written_wall: "2026-09-13T06:10:00+00:00",
  stations: [
    {
      code: "S1_Feeding",
      state: "Suspended",
      category: "waiting-on-others",
      reason: "blocked:B1_2",
    },
    {
      code: "S2_Joining",
      state: "Held",
      category: "held-by-own-fault",
      reason: "jam",
    },
    {
      code: "S3_Inspection",
      state: "Suspended",
      category: "waiting-on-others",
      reason: "starved:B2_3",
    },
    { code: "S4_Outfeed", state: "Execute", category: "producing", reason: "" },
  ],
  buffers: [
    {
      code: "B1_2",
      level: 5,
      capacity: 5,
      upstream: "S1_Feeding",
      downstream: "S2_Joining",
    },
    {
      code: "B2_3",
      level: 0,
      capacity: 5,
      upstream: "S2_Joining",
      downstream: "S3_Inspection",
    },
    {
      code: "B3_4",
      level: 2,
      capacity: 5,
      upstream: "S3_Inspection",
      downstream: "S4_Outfeed",
    },
  ],
};

function tileFor(code: string): HTMLElement {
  const tile = screen.getByText(code).closest("li");
  if (tile === null) throw new Error(`${code} rendered outside a station tile`);
  return tile;
}

describe("the line", () => {
  it("shows the reason a station is waiting", () => {
    render(<Line snapshot={HELD_S2} />);
    // The whole diagnostic value of the screen: *which* buffer, and which direction.
    expect(screen.getByText("starved:B2_3")).toBeInTheDocument();
    expect(screen.getByText("jam")).toBeInTheDocument();
  });

  it("colours by category and names the state as text beside it", () => {
    render(<Line snapshot={HELD_S2} />);

    // ISA-101: the class is the colour and the text is the second channel. Asserting
    // both together is what stops one of them being dropped later as redundant.
    const held = tileFor("S2_Joining");
    expect(held).toHaveClass("station--held-by-own-fault");
    expect(held).toHaveTextContent("Held");

    const starved = tileFor("S3_Inspection");
    expect(starved).toHaveClass("station--waiting-on-others");
    expect(starved).toHaveTextContent("Suspended");

    // The distinction the categories exist for: two stations that are both stopped, and
    // only one of them is worth walking to.
    expect(held.className).not.toEqual(starved.className);
  });

  it("draws every buffer as a fraction of the capacity the payload carries", () => {
    render(<Line snapshot={HELD_S2} />);
    const bars = screen.getAllByRole("meter");
    expect(bars.map((bar) => bar.getAttribute("aria-valuenow"))).toEqual([
      "5",
      "0",
      "2",
    ]);
    // Never a constant in the frontend: this is Settings.buffer_capacity.
    expect(bars.map((bar) => bar.getAttribute("aria-valuemax"))).toEqual([
      "5",
      "5",
      "5",
    ]);
  });

  it("labels simulated time as the clock the numbers belong to", () => {
    render(<Line snapshot={HELD_S2} />);
    expect(screen.getByText("simulated time")).toBeInTheDocument();
    // §4.2: the wall clock is on the screen for diagnostics and must say so, or it
    // reads as a second opinion about when something happened.
    expect(screen.getByText(/wall clock/)).toHaveTextContent(
      "diagnostics only",
    );
  });
});
