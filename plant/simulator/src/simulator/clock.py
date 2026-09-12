"""past -> catch-up -> live (§3.2). All timestamps UTC; shift logic Europe/Berlin."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from simulator.config import ClockConfig

BERLIN = ZoneInfo("Europe/Berlin")
NIGHT_START = time(22, 0)
NIGHT_END = time(6, 0)


class Phase(str, Enum):
    BOOTING = "booting"
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
    end_day = at_local.date()
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
    evening, ~24 h back) and worse still for a boot between 00:00 and 06:00: that
    hour sits inside a night shift still short of its own 06:00 end, so the last
    *completed* one is the night before that -- ~31 h back on an ordinary day, and
    32 h when the span crosses the autumn DST fall-back, which lengthens it by the
    repeated hour.
    """
    start, _ = last_completed_night_shift(at_local)
    return at_local.astimezone(UTC) - start.astimezone(UTC)
