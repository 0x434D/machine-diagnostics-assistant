"""§5.4: a line stop is defined by output, not by state.

No part leaves S4 for longer than the micro-stop threshold and the line has stopped,
whatever the state machines say. Shorter interruptions are counted as micro-stops,
because a rising micro-stop count is its own diagnostic signal.

**Taking the definition from output is the whole point of this module.** A station in
`Aborted` is not a stopped line: the buffers below it keep S4 emitting for as long as
they hold parts, and a run that read `state_changes` instead would report a stop that
began the moment the station shut down, minutes before the line actually stopped
producing — and would report one for every `Suspended` episode the line clears by
itself. The propagation walk in `propagation.py` is what explains a stop once this
module has found one; it is not what finds it.

Pure: instants in, episodes out. No database, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from analysis.windows import Window

DEFAULT_MICRO_STOP_THRESHOLD: Final = timedelta(seconds=60)
"""§5.4's threshold: ten missed cycles of §3.1's 6 s takt. A gap longer than this is a
stop; a gap at or below it is a micro-stop."""

DEFAULT_INTERRUPTION_FLOOR: Final = timedelta(seconds=9)
"""Below which a gap between two parts is an ordinary cycle rather than an interruption.

One and a half takts. Above it because §3.1's takt jitter is Gaussian with σ = 0.05 s, so
9 s is sixty sigma away from a normal cycle and no clean run can reach it; below it
because §3.5's micro-stops add 4 s to 25 s to a single cycle, which puts the shortest
one this must still count at 10 s. Configuration rather than a constant (§10.3): a line
with a different takt has a different floor, and one whose S4 takt is not the line takt
has it here rather than hidden in an inequality.
"""


@dataclass(frozen=True)
class Stop:
    """One interruption of output long enough to be a stop.

    `to_ts` is always set, so a duration is always answerable; `open_at_window_end` is
    what distinguishes a stop the window saw end from one it merely stopped watching.
    Those are different facts, and a stop reported as 90 s long because the window
    closed 90 s in is a different claim from a stop that ended after 90 s.
    """

    from_ts: datetime
    to_ts: datetime
    started_before_window: bool
    open_at_window_end: bool

    @property
    def duration_seconds(self) -> float:
        return (self.to_ts - self.from_ts).total_seconds()


@dataclass(frozen=True)
class StopDetection:
    """Everything §5.4 defines over one window: the stops, and the count of the
    interruptions that were too short to be one."""

    window: Window
    stops: tuple[Stop, ...]
    micro_stops: int


def detect_stops(
    outputs: Iterable[datetime],
    window: Window,
    *,
    micro_stop_threshold: timedelta = DEFAULT_MICRO_STOP_THRESHOLD,
    interruption_floor: timedelta = DEFAULT_INTERRUPTION_FLOOR,
) -> StopDetection:
    """Find the stops and count the micro-stops in `outputs` over `window`.

    `outputs` is every instant at which a part left S4, in any order; instants outside
    the half-open window are ignored, so a part landing exactly on `to_ts` belongs to
    the next window and cannot close this one's open stop.

    A gap longer than `micro_stop_threshold` is a stop. A gap that exceeds
    `interruption_floor` without reaching it is a micro-stop. Both window edges are
    gap boundaries: the line was producing nothing between `from_ts` and the first part
    either, and a window that opens into silence has to say so rather than start
    counting at the first part it happens to see.
    """
    parts = sorted(moment for moment in outputs if window.contains(moment))
    boundaries = [window.from_ts, *parts, window.to_ts]

    stops: list[Stop] = []
    micro_stops = 0
    for index in range(len(boundaries) - 1):
        began, ended = boundaries[index], boundaries[index + 1]
        gap = ended - began
        if gap <= interruption_floor:
            continue
        if gap <= micro_stop_threshold:
            micro_stops += 1
            continue
        stops.append(
            Stop(
                from_ts=began,
                to_ts=ended,
                started_before_window=index == 0,
                open_at_window_end=index == len(boundaries) - 2,
            )
        )

    return StopDetection(window=window, stops=tuple(stops), micro_stops=micro_stops)
