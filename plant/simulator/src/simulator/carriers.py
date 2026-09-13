"""§3.1's circulating pool of carriers.

The circulation is the point. A carrier that passes once carries its wear out of the
population with it, and M2c's scenario 4 -- carrier 7 wears, and `misalignment` plus
`scratch` concentrate on it -- has no statistical signal to find. FIFO rather than a
stack for the same reason: a stack would recirculate whichever carrier came back last
while the rest sat idle.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Carrier:
    carrier_id: int


class CarrierPool:
    def __init__(self, count: int) -> None:
        if count < 1:
            raise ValueError(f"a line needs at least one carrier, got {count}")
        self._count = count
        self._free: deque[Carrier] = deque(Carrier(i) for i in range(count))

    @property
    def count(self) -> int:
        return self._count

    @property
    def available(self) -> int:
        return len(self._free)

    def acquire(self) -> Carrier | None:
        """None when every carrier is in the line. S1 must then stop rather than
        conjure one -- a pool that grew on demand would make the carrier count
        decorative, and §3.1 makes it one of the three numbers that set line
        behaviour."""
        return self._free.popleft() if self._free else None

    def release(self, carrier: Carrier) -> None:
        self._free.append(carrier)
