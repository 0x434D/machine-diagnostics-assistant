"""§3.1's buffers, and the starved/blocked rule §3.3 derives from their levels.

Buffer capacity is the number that sets how long propagation takes to become
visible: five carriers at a 6 s takt means S3 starves roughly 30 s after S2 stops.
That delay is what M3's analysis has to reason about, so it is configuration
(`Settings.buffer_capacity`) rather than a constant here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from simulator.carriers import Carrier
from simulator.packml import SuspendReason


@dataclass
class Buffer:
    """One buffer between two stations. It holds carriers, not a count.

    A count would be enough for M2a, where nothing yet asks which carrier is where.
    It would not survive M2b: an assembly serial is created at S1 and travels on its
    carrier, so "which part is at S3" has to be answerable from the buffer itself
    rather than reconstructed by time-joining the takt series -- which is exactly the
    inference §3.4a forbids.

    `upstream` and `downstream` are station codes rather than station objects: §4.1
    exposes them as OPC UA references the gateway browses to discover the line's
    topology, and a code is what crosses that wire.

    `_carriers` takes `init=False`: the constructor's contract is the four fields
    named in the Task 3 interface (buffer_id, capacity, upstream, downstream) and
    nothing else. Leaving it a settable constructor argument would let a caller hand
    a buffer more carriers than its own capacity before a single `put()` ever checked
    -- the capacity invariant would hold everywhere except the one place state enters
    the object.
    """

    buffer_id: str
    capacity: int
    upstream: str
    downstream: str
    _carriers: deque[Carrier] = field(default_factory=deque, init=False, repr=False)

    @property
    def level(self) -> int:
        """§4.1's `Level` node. Derived from the contents rather than tracked
        alongside them, so the two can never disagree."""
        return len(self._carriers)

    @property
    def is_empty(self) -> bool:
        return self.level == 0

    @property
    def is_full(self) -> bool:
        return self.level >= self.capacity

    def put(self, carrier: Carrier) -> None:
        if self.is_full:
            raise ValueError(f"{self.buffer_id} is full at {self.capacity}")
        self._carriers.append(carrier)

    def take(self) -> Carrier:
        """FIFO: a buffer is a queue on a conveyor, not a stack."""
        if self.is_empty:
            raise ValueError(f"{self.buffer_id} is empty")
        return self._carriers.popleft()


def suspend_reason_for(
    upstream: Buffer | None, downstream: Buffer | None
) -> SuspendReason | None:
    """§3.3's rule: a station is `starved` when the buffer feeding it is empty, and
    `blocked` when the buffer it discharges into is full.

    `None` on either side means the line ends there -- S1 is fed by feeder lanes and
    S4 discharges to outfeed -- and a station is never suspended for a buffer that
    does not exist. M2c's scenarios 1 and 2 inject faults on exactly those outer
    sides, and they must present as something other than a buffer condition.

    Starvation wins when both hold. The station has no part to work on either way,
    and naming the downstream blockage would point diagnosis at the wrong end of a
    line that is actually short of parts.
    """
    if upstream is not None and upstream.is_empty:
        return SuspendReason("starved", upstream.buffer_id)
    if downstream is not None and downstream.is_full:
        return SuspendReason("blocked", downstream.buffer_id)
    return None
