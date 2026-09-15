"""§4.4 / §5.3's `/coverage`: where a window's data can be trusted, and where it cannot.

Gap markers exist so that missing data is never mistaken for a quiet machine (§4.4). The
guard this module computes is §6.1 step 3: before the agent answers from a window, it must
know whether the window is trustworthy, partially trustworthy, or not trustworthy at all.
Folding that into a count would let a caller read "zero events, fully covered" and "zero
events, ingest was down" as the same thing, which is the exact confusion the endpoint exists
to prevent — so the two are kept apart as data, not left for a caller to infer from a list.

Pure: no database, no HTTP, no clock read. The caller queries `ingest_gaps` and passes the
rows in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from analysis.windows import Window


@dataclass(frozen=True)
class GapInterval:
    """One interval where ingestion was not happening — an `ingest_gaps` row, or a clipped
    piece of one in a `Coverage.gaps` list.

    Half-open like `Window`, on the same invariant: a gap ending exactly at another
    interval's start does not overlap it. Not re-validated here — `ingest_gaps` rows are
    written by the gateway under that same invariant, and `Window` already enforces it at
    the one place a caller-supplied boundary enters this module.
    """

    from_ts: datetime
    to_ts: datetime
    reason: str


@dataclass(frozen=True)
class Coverage:
    """The coverage report for one requested `Window`.

    `fully_covered` is a property rather than a field a constructor could set
    inconsistently with `covered_fraction`, and it is exposed at all — rather than left for
    a caller to derive from `gaps` — because `gaps == ()` and "fully covered" are only the
    same fact when nothing was ever clipped into `gaps` in the first place: a caller that
    forgets to check would confuse a quiet window with a blackout the moment gap rows exist
    but every one of them lies outside the window, and a boolean field would carry the same
    risk if it could be set independently of the fraction it must agree with.
    """

    window: Window
    # Sorted by `from_ts`, matching the `ingest_gaps` query's own ordering (routes_inspection
    # .py) so a caller diffing this against the folded `/inspection/stats.coverage` field
    # sees the same order in both places.
    gaps: tuple[GapInterval, ...]
    covered_fraction: float

    @property
    def fully_covered(self) -> bool:
        return self.covered_fraction == 1.0


def compute_coverage(window: Window, gaps: Iterable[GapInterval]) -> Coverage:
    """Coverage of `window` given every ingest-gap interval that might overlap it.

    Assumes each `gaps` entry is a valid half-open interval in the same tz-aware UTC as
    `window`; `gaps` need not be pre-filtered to those that actually overlap `window` — a
    gap entirely outside it contributes nothing and is silently dropped, which is what lets
    a caller pass a query's result straight through. Raises nothing of its own: `window`'s
    own constructor is where a malformed boundary would already have failed.
    """
    clipped: list[GapInterval] = []
    for gap in gaps:
        start = max(gap.from_ts, window.from_ts)
        end = min(gap.to_ts, window.to_ts)
        # start < end is exactly the half-open overlap test (equivalent to `gap.from_ts <
        # window.to_ts and gap.to_ts > window.from_ts`) and also the clip: a gap the window
        # only partially contains comes out bounded by the window, never at its own extent.
        if start < end:
            clipped.append(GapInterval(from_ts=start, to_ts=end, reason=gap.reason))
    clipped.sort(key=lambda gap: gap.from_ts)

    uncovered_seconds = _merged_duration_seconds(clipped)
    # window.duration_seconds > 0 always holds: Window rejects a non-forward interval in its
    # own constructor, so there is no window this division could see as zero-width.
    covered_fraction = 1.0 - uncovered_seconds / window.duration_seconds

    return Coverage(
        window=window, gaps=tuple(clipped), covered_fraction=covered_fraction
    )


def _merged_duration_seconds(intervals_sorted_by_start: Sequence[GapInterval]) -> float:
    """Total seconds covered by the union of already-window-clipped, start-sorted intervals.

    Two `ingest_gaps` rows can touch or overlap — an overflow gap recorded back-to-back with
    a reconnect gap, say — and summing their durations independently would count the shared
    stretch twice, understating `covered_fraction` for a stretch of missing data no wider
    than either gap alone. Merging first is what keeps the fraction an accurate measure of
    wall-clock time rather than of how many gap rows happen to describe it.
    """
    if not intervals_sorted_by_start:
        return 0.0

    total = 0.0
    span_start = intervals_sorted_by_start[0].from_ts
    span_end = intervals_sorted_by_start[0].to_ts
    for interval in intervals_sorted_by_start[1:]:
        if interval.from_ts <= span_end:
            span_end = max(span_end, interval.to_ts)
        else:
            total += (span_end - span_start).total_seconds()
            span_start, span_end = interval.from_ts, interval.to_ts
    total += (span_end - span_start).total_seconds()
    return total
