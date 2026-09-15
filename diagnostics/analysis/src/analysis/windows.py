"""The half-open time window every analysis endpoint takes.

Defined once and imported everywhere rather than re-declared per module: the
window is the single parameter shape the whole §5.3 endpoint list shares, and
two definitions of it would drift in exactly the way that produces an
off-by-one nobody can see in a test that uses the same definition twice.

Half-open `[from_ts, to_ts)` matches what M1 and M2 already built, so a part
landing exactly on a boundary belongs to one window and not to both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Window:
    """A half-open UTC interval: `from_ts` inclusive, `to_ts` exclusive.

    Assumes both bounds are timezone-aware. Raises ValueError if either is
    naive or if the interval is not strictly forward — a backwards window is
    always a caller bug and answering it with an empty result would hide one.
    """

    from_ts: datetime
    to_ts: datetime

    def __post_init__(self) -> None:
        for name, value in (("from_ts", self.from_ts), ("to_ts", self.to_ts)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware; got {value!r}")
        if self.from_ts >= self.to_ts:
            raise ValueError(
                f"window must run forwards; got {self.from_ts} .. {self.to_ts}"
            )

    @property
    def duration_seconds(self) -> float:
        return (self.to_ts - self.from_ts).total_seconds()

    def contains(self, moment: datetime) -> bool:
        return self.from_ts <= moment < self.to_ts

    def is_closed(self, now: datetime) -> bool:
        """Whether the window lies wholly in the past.

        §5.3 caches a closed window indefinitely because its result can never
        change; only a window still being written to needs a TTL.
        """
        return self.to_ts <= now

    @classmethod
    def utc(cls, from_ts: datetime, to_ts: datetime) -> Window:
        return cls(from_ts.astimezone(UTC), to_ts.astimezone(UTC))
