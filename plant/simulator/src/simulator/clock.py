"""past -> catch-up -> live (§3.2). All timestamps UTC; shift logic Europe/Berlin."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from simulator.config import ClockConfig

BERLIN = ZoneInfo("Europe/Berlin")

# A shared semantic definition, not a plant tuning knob: M3's diagnostics-side
# /time/resolve needs these same 22:00/06:00 values. Keeping them as constants here
# rather than in Settings avoids two sources of truth for the same shift boundary
# across the stack split.
NIGHT_START = time(22, 0)
NIGHT_END = time(6, 0)


class Phase(str, Enum):
    CATCHUP = "catchup"
    LIVE = "live"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SimulatedClock:
    """Simulated time starts history_depth in the past and closes the gap at
    catchup_speed, then tracks the wall clock at exactly 1.0.

    Simulated time gains (speed - 1) seconds per wall second, so the gap closes
    after history_depth / (speed - 1).
    """

    def __init__(
        self, cfg: ClockConfig, wall_fn: Callable[[], datetime] = _utc_now
    ) -> None:
        if cfg.catchup_speed <= 1.0:
            raise ValueError("catchup_speed must exceed 1.0 or history never closes")
        self._cfg = cfg
        self._wall = wall_fn
        self._boot = wall_fn()

    @property
    def history_depth(self) -> timedelta:
        """How far before boot simulated time starts. Task 4 needs this to size
        the history it generates; kept public so callers never reach into `_cfg`."""
        return self._cfg.history_depth

    @property
    def catchup_speed(self) -> float:
        """The multiple at which simulated time closes the gap to the wall clock
        during Phase.CATCHUP. Kept public for the same reason as history_depth."""
        return self._cfg.catchup_speed

    @property
    def boot_wall(self) -> datetime:
        return self._boot

    @property
    def history_start(self) -> datetime:
        return self._boot - self._cfg.history_depth

    @property
    def catchup_duration(self) -> timedelta:
        return self._cfg.history_depth / (self._cfg.catchup_speed - 1.0)

    @property
    def phase(self) -> Phase:
        return (
            Phase.CATCHUP
            if self._wall() < self._boot + self.catchup_duration
            else Phase.LIVE
        )

    def now(self) -> datetime:
        wall = self._wall()
        if wall >= self._boot + self.catchup_duration:
            return wall
        elapsed = (wall - self._boot).total_seconds()
        return self.history_start + timedelta(seconds=elapsed * self._cfg.catchup_speed)


def last_completed_night_shift(at_local: datetime) -> tuple[datetime, datetime]:
    """The most recent night shift (22:00-06:00 local) that has already ended."""
    # Correct by construction rather than by coincidence: at_local.date() below reads
    # a *local* calendar date, and normalising first keeps that right for any input
    # offset. Without this, the `end > at_local` repair below happens to catch a wrong
    # date guess, but only for offsets within ~6 h of Berlin's own.
    at_local = at_local.astimezone(BERLIN)
    end_day = at_local.date()
    # datetime.combine's default fold=0 would be wrong for a local time inside
    # Berlin's ambiguous 02:00-03:00 autumn hour; it is safe here only because 06:00
    # and 22:00 never fall inside that window.
    end = datetime.combine(end_day, NIGHT_END, tzinfo=BERLIN)
    if end > at_local:
        end = datetime.combine(end_day - timedelta(days=1), NIGHT_END, tzinfo=BERLIN)
    start = datetime.combine(end.date() - timedelta(days=1), NIGHT_START, tzinfo=BERLIN)
    return start, end


def required_history_depth(at_local: datetime) -> timedelta:
    """How deep history must reach for the last completed night shift to lie
    fully inside it, if the plant boots at at_local.

    §3.2 asserts 18 h suffices. It does for a boot after a night shift's own 06:00
    end. It is worse for an evening boot (last completed night started the previous
    evening, ~24 h back) and worst of all approaching a boot at 06:00 itself: that
    instant sits at the far edge of a night shift still (barely) short of its own
    06:00 end, so the last *completed* one is the night before that -- the ~24 h day
    gap back to it, plus that whole night's own length. The supremum over a year is
    24 h + 9 h = 33 h, approached but never quite reached as boot -> 06:00 on the
    autumn DST Sunday, the one night 9 h long instead of the usual 8. This function
    is piecewise linear with slope 1 and resets at each local 06:00, so every day's
    maximum sits at that right-hand boundary, not somewhere an hourly sample would
    land.
    """
    start, _ = last_completed_night_shift(at_local)
    return at_local.astimezone(UTC) - start.astimezone(UTC)
