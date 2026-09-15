import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Line } from "../Line";
import { StationTile } from "../StationTile";
import type { Category, LineSnapshot, StationView } from "../snapshot";
// Vite's ?raw, not node:fs: this is the same resolver the bundle is built with, so the
// file asserted here is the file that ships rather than one found by walking upwards
// from a test's own location.
import sheet from "../styles.css?raw";

/** §15's five, mirroring `hmi.CATEGORIES`. The Python side proves every PackML state maps
 * into this set; this side proves each member of it reaches the screen as its own class
 * and has a rule to render with. */
const CATEGORIES: Category[] = [
  "producing",
  "waiting-on-others",
  "held-by-own-fault",
  "stopped",
  "transitioning",
];

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
      browse_name: "S1_Feeding",
      state: "Suspended",
      category: "waiting-on-others",
      reason: "blocked:B1_2",
    },
    {
      browse_name: "S2_Joining",
      state: "Held",
      category: "held-by-own-fault",
      reason: "jam",
    },
    {
      browse_name: "S3_Inspection",
      state: "Suspended",
      category: "waiting-on-others",
      reason: "starved:B2_3",
    },
    {
      browse_name: "S4_Outfeed",
      state: "Execute",
      category: "producing",
      reason: "",
    },
  ],
  buffers: [
    {
      code: "B1_2",
      level: 5,
      capacity: 5,
      upstream_browse_name: "S1_Feeding",
      downstream_browse_name: "S2_Joining",
    },
    {
      code: "B2_3",
      level: 0,
      capacity: 5,
      upstream_browse_name: "S2_Joining",
      downstream_browse_name: "S3_Inspection",
    },
    {
      code: "B3_4",
      level: 2,
      capacity: 5,
      upstream_browse_name: "S3_Inspection",
      downstream_browse_name: "S4_Outfeed",
    },
  ],
  parts: [
    {
      serial: "A-00000412",
      at: "2026-09-13T06:09:54+00:00",
      disposition: "reject",
      reason: "gap",
      image_url: "/parts/A-00000412/image",
    },
    {
      serial: "A-00000411",
      at: "2026-09-13T06:09:48+00:00",
      disposition: "good",
      reason: "",
      image_url: null,
    },
  ],
  alarms: [
    {
      sequence: 4,
      station_browse_name: "S2_Joining",
      code: "A-207",
      text: "joining force out of tolerance",
      severity: 700,
      raised_at: "2026-09-13T06:09:12+00:00",
      acknowledged: false,
      acked_at: null,
    },
  ],
};

function tileFor(browseName: string): HTMLElement {
  // Scoped to `.station__name`, because a station's browse name appears twice on a
  // screen with an active alarm on it — once on the tile and once on the alarm row that
  // names the station it was raised at — and an unscoped lookup finds both.
  const named = screen
    .getAllByText(browseName)
    .find((node) => node.classList.contains("station__name"));
  const tile = named?.closest("li") ?? null;
  if (tile === null)
    throw new Error(`${browseName} rendered outside a station tile`);
  return tile;
}

function stationIn(category: Category): StationView {
  return {
    browse_name: `station-${category}`,
    state: "Execute",
    category,
    reason: "",
  };
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

  it("gives every category its own class", () => {
    // `stopped` and `transitioning` reach a screen only on a restart or a recovery, so
    // neither appears in the fixture above and neither had a test. A typo in either
    // rendered as the fallback and nothing failed — which is exactly the "renders as
    // nothing" case the Python exhaustiveness test exists to prevent.
    for (const category of CATEGORIES) {
      const { unmount } = render(<StationTile station={stationIn(category)} />);
      expect(tileFor(`station-${category}`)).toHaveClass(
        `station--${category}`,
      );
      unmount();
    }
  });

  it("gives every category a rule to render with", () => {
    // jsdom applies no stylesheet, so the class assertion above cannot see whether the
    // class resolves to anything. Reading the sheet is the only representation there is,
    // and a category with no rule renders as --no-such-category rather than as a
    // plausible neighbour.
    for (const category of CATEGORIES) {
      expect(sheet).toContain(`.station--${category} {`);
      expect(sheet).toContain(`--${category}:`);
    }
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

  it("lists an active alarm with the code and text §5.2 keeps", () => {
    render(<Line snapshot={HELD_S2} />);
    const alarm = screen.getByTestId("alarm");
    // The code is what M4's knowledge base is keyed on and the text is what an operator
    // reads; neither stands in for the other.
    expect(alarm).toHaveTextContent("A-207");
    expect(alarm).toHaveTextContent("joining force out of tolerance");
    expect(alarm).toHaveTextContent("S2_Joining");
    // Whether anyone has been to it is said in words, not only in the class — the same
    // rule the station tiles follow (ISA-101).
    expect(alarm).toHaveClass("alarm--new");
    expect(alarm).toHaveTextContent("not acknowledged");
  });

  it("says so rather than rendering nothing when no alarm is active", () => {
    // No active alarms is the normal state of a line, and a blank area reads as a panel
    // that failed to load.
    render(<Line snapshot={{ ...HELD_S2, alarms: [] }} />);
    expect(screen.queryByTestId("alarm")).toBeNull();
    expect(screen.getByText("no active alarms")).toBeInTheDocument();
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
