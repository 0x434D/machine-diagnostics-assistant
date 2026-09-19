/** §7.2's plant status banner — *connected · backfilling · plant offline, history to 03:14*.
 *
 * The one claim this architecture exists to make is that the diagnostics stack answers with
 * the plant shut down, and until this banner existed a reader looking at an answer could not
 * tell which of those situations produced it. Demonstrating it meant stopping a container;
 * this makes it visible.
 *
 * **It reports what the gateway says, and its own failure is not one of the states.** "The
 * gateway could not be reached" and "the gateway says the plant is offline" are opposite
 * facts — the first is about this stack, the second about the plant — and a banner that
 * collapsed them would report the one outage as the other. Colour is never the only channel
 * (ISA-101): every reading below is a word, a glyph and a hue.
 *
 * **It stands over the whole shell rather than over the answer.** The reading qualifies the
 * stop timeline and the part record exactly as much as it qualifies an answer, so `AppShell`
 * is its home — which means every view now issues one request this application did not make
 * before. The three view tests that assert *nothing was read* say so about the analysis
 * service rather than about `fetch`, because "no window means no history is read" was always
 * the claim and "this screen makes no request at all" never was.
 */
import { useEffect, useState, type ReactNode } from "react";

import { fetchPlantStatus, type PlantStatus } from "../api";
import { useAuth } from "../AuthContext";
import { useResolution } from "../citations/useResolution";

/** How often the banner asks again.
 *
 * A status read once at page load and then left alone is the quiet wrong answer in
 * miniature: the plant goes down, and the screen keeps saying "connected" under every answer
 * the reader opens afterwards. Fifteen seconds is short against the time it takes to read an
 * answer and long against the four GETs a minute it costs.
 */
const POLL_MS = 15_000;

interface Reading {
  /** The word. This, not the colour, is what says which situation the reader is in. */
  readonly label: string;
  readonly glyph: string;
  readonly sentence: string;
}

/** The gateway's five states, in the reader's terms rather than the boundary's.
 *
 * Keyed by the gateway's own spelling and looked up rather than switched on, so a sixth
 * state falls through to `UNRECOGNISED` instead of matching a default branch that reads
 * like knowledge.
 */
const READINGS: Record<string, Reading> = {
  live: {
    label: "Connected",
    glyph: "●",
    sentence:
      "The gateway holds a session with the plant and is recording it as it runs.",
  },
  backfilling: {
    label: "Backfilling",
    glyph: "⟳",
    sentence:
      "Reading the history the gateway was not there for. Until it finishes, a window reaching back into the outage is still being filled in.",
  },
  waiting_for_history: {
    label: "Waiting for the plant's history",
    glyph: "⋯",
    sentence:
      "The plant is building its history at catch-up speed, and the gateway reads none of it until that finishes (§4.3).",
  },
  connecting: {
    label: "Connecting",
    glyph: "◌",
    sentence: "No session with the plant yet.",
  },
  disconnected: {
    label: "Plant offline",
    glyph: "✕",
    sentence:
      "No session with the plant. Everything on this screen is answered from what was already recorded — which is the point, not a degradation.",
  },
};

/** A state this application has no reading for. Loud and its own colour, for the reason
 * `StateBadge` gives: a state rendered in one of the five plausible hues is a screen
 * claiming to understand something it does not. */
const UNRECOGNISED: Reading = {
  label: "Unrecognised gateway state",
  glyph: "?",
  sentence:
    "The gateway reports a state this application has no reading for, so nothing here should be taken as a statement about the plant.",
};

export function PlantStatusBanner() {
  const { token } = useAuth();
  if (token === null) {
    // The same rule the question box applies two files away: §10.5 has every diagnostics
    // endpoint refuse an unauthenticated request, so asking is a round trip whose 401 is
    // already known — and a 401 rendered here would read as "the gateway is unreachable",
    // which is a claim about the boundary rather than about this browser.
    return (
      <Frame state="unauthenticated" glyph="◌" label="Plant status not read">
        Not signed in, so the gateway has not been asked.
      </Frame>
    );
  }
  return <FromGateway />;
}

function FromGateway() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      setTick((previous) => previous + 1);
    }, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, []);

  const resolution = useResolution(`plant-status:${String(tick)}`, (token) =>
    fetchPlantStatus(token),
  );

  // The last reading, held across a refresh. Without it the banner falls back to "reading…"
  // every POLL_MS, which is a flicker that tells nobody anything — and against a slow
  // gateway it would spend most of its life there.
  const [held, setHeld] = useState<PlantStatus | null>(null);
  useEffect(() => {
    if (resolution.state === "open") setHeld(resolution.value);
  }, [resolution]);

  if (resolution.state === "failed") {
    return (
      <Frame state="unreadable" glyph="!" label="Plant status unknown">
        {resolution.reason} — that is a fact about this stack, and says nothing
        about whether the plant is running.
      </Frame>
    );
  }

  const status = resolution.state === "open" ? resolution.value : held;
  if (status === null) {
    return (
      <Frame state="reading" glyph="◌" label="Reading the plant's status">
        Asking the gateway where the boundary stands.
      </Frame>
    );
  }
  return <Known status={status} />;
}

function Known({ status }: { status: PlantStatus }) {
  const known = READINGS[status.state];
  const reading = known ?? UNRECOGNISED;

  return (
    <Frame
      state={known === undefined ? "unrecognised" : status.state}
      glyph={reading.glyph}
      label={reading.label}
    >
      {/* The progress belongs inside the sentence rather than beside it: "backfilling" with
          no idea how far through is a state a reader cannot act on. */}
      {status.state === "backfilling"
        ? `${reading.sentence} ${percent(status.backfillProgress)} of it read.`
        : reading.sentence}
      <span className="plant-status__history">{historyLine(status)}</span>
      {/* The gateway's own word, verbatim beside the sentence written for it. The reading
          above is this application's; this is what the boundary actually said. */}
      <span className="plant-status__raw mono">gateway: {status.state}</span>
      <Notes status={status} />
    </Frame>
  );
}

/** How far the recorded history reaches, which is the half of *"plant offline"* that tells a
 * reader whether the answer in front of them is current.
 *
 * Null is not "the history is empty". `lastEventSourceTs` is the last event **this gateway
 * process** read, so a gateway restarted while the plant is down reports null over a
 * database full of history — and "there is no history" would be false there.
 */
function historyLine(status: PlantStatus): string {
  return status.lastEventSourceTs === null
    ? "This gateway has read no event since it started, so it cannot say how far history reaches."
    : `History to ${status.lastEventSourceTs} — plant time, not the wall clock.`;
}

/** The three conditions that change how everything else on the screen should be read. Each
 * is rendered only when it has something to say, because a row of zeroes under every answer
 * is a row nobody reads on the day one of them is not zero. */
function Notes({ status }: { status: PlantStatus }) {
  const notes: string[] = [];
  if (status.queueDepth > 0) {
    notes.push(
      `${String(status.queueDepth)} records are queued at the gateway and are not in the database yet, so the most recent minutes are not answerable.`,
    );
  }
  if (status.overflowCount > 0) {
    notes.push(
      `${String(status.overflowCount)} subscription overflows: the plant told the gateway it had missed values (§4.4).`,
    );
  }
  if (!status.clockAvailable) {
    notes.push(
      "The plant publishes no clock phase, so the gateway cannot tell catch-up from live and may have backfilled a history that was still being written (§4.3).",
    );
  }

  if (notes.length === 0) return null;
  return (
    <ul className="plant-status__notes">
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

function percent(fraction: number): string {
  return `${String(Math.round(fraction * 100))}%`;
}

function Frame({
  state,
  glyph,
  label,
  children,
}: {
  state: string;
  glyph: string;
  label: string;
  children: ReactNode;
}) {
  return (
    // `role="status"` rather than `role="alert"`: it refreshes on its own every POLL_MS, and
    // an assertive live region would interrupt a screen reader mid-answer to repeat what it
    // already said. `data-state` is what the stylesheet colours from.
    <div className="plant-status" data-state={state} role="status">
      <span className="plant-status__glyph" aria-hidden="true">
        {glyph}
      </span>
      <span className="plant-status__label">{label}</span>
      {/* A div rather than a span: the notes below are a list, and a list inside a span is
          markup a browser is entitled to reshuffle. */}
      <div className="plant-status__detail">{children}</div>
    </div>
  );
}
