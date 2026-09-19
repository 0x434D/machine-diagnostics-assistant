/** How far the line's own clock has actually reached, beside a window from the calendar.
 *
 * `/time/resolve` answers a calendar question and is right about shifts and DST, but it says
 * nothing about where the *data* ends. All analysis reads `SourceTimestamp`, and
 * `/line/status` is explicit that simulated time may sit **ahead** of the wall clock — a
 * negative staleness, reported rather than clamped. Without this line a reader resolving
 * "this shift" would have to assume the two agree, and a containment scoped on that
 * assumption is the quiet wrong answer this whole application exists to refuse.
 *
 * Nothing here is computed. `staleness_seconds` and the threshold it was judged against are
 * both the service's, and so is the verdict; the only choice made below is which word the
 * sign of that number gets, because a reader cannot act on "-3600".
 */
import { fetchLineStatus, type LineStatus } from "../api";
import { useResolution } from "../citations/useResolution";

export function LineClock() {
  const resolution = useResolution("line-status", (token) =>
    fetchLineStatus(token),
  );

  if (resolution.state === "opening") {
    return (
      <p className="shift__clock" data-testid="line-clock">
        Reading how far the line&apos;s own clock has run…
      </p>
    );
  }

  if (resolution.state === "failed") {
    // Not the same as "the line is stopped". Nobody looked, so this screen has nothing to
    // say about where the data ends and must not leave the window above reading as verified.
    return (
      <p className="shift__clock evidence__missing" data-testid="line-clock">
        The line&apos;s own clock could not be read: {resolution.reason} The
        window above is the calendar&apos;s; nothing here says how far the data
        goes.
      </p>
    );
  }

  return (
    <p className="shift__clock" data-testid="line-clock">
      {describe(resolution.value)}
    </p>
  );
}

function describe(status: LineStatus): string {
  if (status.latest_data_at === null) {
    // §5.3: a null staleness is a database with no row at all — a fresh deployment, which
    // is a different fact from a stale one and calls for a different action.
    return "No row has ever reached the diagnostics stack, so the line's clock has not started and any window will come back empty.";
  }

  const verdict = status.live
    ? `the service reads that as live against its ${String(status.live_within_seconds)} s threshold`
    : `the service reads that as not live against its ${String(status.live_within_seconds)} s threshold`;

  if (status.staleness_seconds === null) {
    return `The newest row the line has is at ${status.latest_data_at}, and ${verdict}.`;
  }

  const seconds = Math.abs(status.staleness_seconds).toFixed(0);
  const sense =
    status.staleness_seconds < 0
      ? `${seconds} s ahead of ${status.as_of} — simulated time is running ahead of the wall clock, which is a fact about the plant's clock rather than an error`
      : `${seconds} s behind ${status.as_of}`;

  return `The newest row the line has is at ${status.latest_data_at}, ${sense}; ${verdict}.`;
}
