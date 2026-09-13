"""D1: the line advances on a discrete-event queue, not on four concurrent loops.

§3.6 requires that the same seed plus the same scenario reproduces a run exactly.
Four `asyncio` tasks drawing from one RNG and racing on buffer levels cannot promise
that -- task interleaving orders the draws. A single queue popped in time order can,
and it handles independently jittered per-station takt naturally, which a fixed
global tick does not.

The queue also collapses catch-up and live into one mechanism: catch-up drains it as
fast as it can, live sleeps until each cycle's wall-clock equivalent (§4.3, and
Task 7).
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from simulator.buffers import Buffer, suspend_reason_for
from simulator.carriers import Carrier, CarrierPool
from simulator.packml import Command, State, StateMachine, SuspendReason

CARRIER_RETURN = "carrier-return"
"""What S1 names when it suspends for want of a free carrier.

Not one of §4.1's three buffers, and deliberately not dressed up as one: an empty pool
and a full B1_2 are different conditions with different fixes, and only one of them is
a buffer. Stop S4 and the line parks its carriers downstream -- B3_4 fills, then B2_3
-- so S1 runs out of carriers while B1_2 still has room, and a station reporting
`starved:B1_2` there would be naming a condition that is not true.

B1_2 does fill in free running; §3.1 designs for it, and S1 reports `blocked:B1_2`
when it does. Both conditions are real and both are reachable, which is why S1 needs a
name for each rather than one name stretched over both.
"""


class StationCycle(Protocol):
    """What the Line needs from a station. Four implementations, in `stations/`.

    Deliberately narrow: the Line owns movement -- which buffer a carrier comes from,
    which it goes to, whether the station may run at all -- and the station owns only
    what happens to a part while it is there. Splitting it the other way would put
    buffer logic in four places.
    """

    @property
    def code(self) -> str: ...

    async def run_cycle(
        self, at: datetime, carrier: Carrier, part: PartState
    ) -> None: ...

    async def publish_state(self, at: datetime, state: State, reason: str) -> None: ...

    def next_takt(self) -> float: ...


@dataclass
class PartState:
    """What is known about the part riding a carrier, while it rides.

    Keyed by carrier id on the Line rather than stored in the buffers, because that
    is what a real line does -- the carrier has a tag and the part is whatever is
    currently on it. S3 writes the disposition; S4 reads it to drive GoodCount and
    RejectCount (§4.1), which is the only cross-station fact M2a needs.

    M2b replaces this with a real assembly serial and its genealogy. It is kept this
    thin on purpose: anything richer here would be M2b's data model arriving early
    and unvalidated.
    """

    disposition: str | None = None


@dataclass(frozen=True)
class CycleOutcome:
    station_code: str
    at: datetime
    produced: bool
    state: State
    reason: str
    next_at: datetime


class CycleQueue:
    """Deterministic by construction: the heap key is
    `(at, -station_index, station_index)` -- time first, then downstream-first."""

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, int]] = []

    def schedule(self, at: datetime, station_index: int) -> None:
        # -station_index, so that stations due at the same instant run downstream
        # first. Upstream-first would let a carrier loaded by S1 be taken by S2, S3
        # and S4 within one instant -- a part crossing the whole line in zero
        # simulated time. Downstream-first makes a part advance exactly one station
        # per takt, which is what a conveyor does. The third element is the index
        # itself, so the caller never has to un-negate it.
        heapq.heappush(self._heap, (at, -station_index, station_index))

    def pop(self) -> tuple[datetime, int] | None:
        if not self._heap:
            return None
        at, _, station_index = heapq.heappop(self._heap)
        return at, station_index

    def __len__(self) -> int:
        return len(self._heap)


class Line:
    """Four stations, three buffers, one circulating carrier pool, one queue.

    `stations` is in line order: index 0 feeds, index -1 discharges. `buffers[i]`
    sits between `stations[i]` and `stations[i+1]`, so a station's source and sink
    are derived from its position rather than configured -- which is the same claim
    §4.1 makes about the gateway discovering topology rather than being told it.
    """

    def __init__(
        self,
        stations: Sequence[StationCycle],
        buffers: Sequence[Buffer],
        carriers: CarrierPool,
    ) -> None:
        if len(buffers) != len(stations) - 1:
            raise ValueError(
                f"{len(stations)} stations need {len(stations) - 1} buffers between "
                f"them, got {len(buffers)}"
            )
        self._stations = list(stations)
        self._buffers = list(buffers)
        self._carriers = carriers
        self._queue = CycleQueue()
        # Carrier id -> the part currently riding it. A carrier holds at most one
        # part, so the id is a sufficient key and no buffer has to carry a payload.
        self._parts: dict[int, PartState] = {}
        # Every station starts Aborted and is walked up through the real sequence
        # rather than constructed straight into Execute, so each machine's own history
        # is the one that actually happened. Nothing is published here -- __init__ is
        # sync and publish_state is not -- so the *observable* history still begins at
        # an Execute nothing caused. Task 7's driver owns the async side and is where
        # these bring-up transitions get emitted.
        self._machines = {station.code: StateMachine() for station in stations}
        for machine in self._machines.values():
            machine.apply(Command.CLEAR)
            machine.settle()
            machine.apply(Command.RESET)
            machine.settle()
            machine.apply(Command.START)
            machine.settle()

    @property
    def buffers(self) -> Sequence[Buffer]:
        return self._buffers

    def machine_for(self, code: str) -> StateMachine:
        return self._machines[code]

    def seed(self, start_ts: datetime) -> None:
        """Schedule every station's first cycle at the same instant. Downstream-first
        ordering is what turns that into a line starting up rather than a part
        teleporting."""
        for index in range(len(self._stations)):
            self._queue.schedule(start_ts, index)

    def hold(self, code: str, reason: str) -> None:
        """Put a station into `Held` -- §3.3's cause candidate, which does not clear
        itself. M2c injects faults through this; M2a's propagation proof uses it to
        stop S2 and watch S3 starve.

        Raises ValueError if the station is not in `Execute`, which is the only state
        PackML accepts Hold from. A running station leaves `Execute` whenever its
        buffers suspend it -- a couple of percent of steps -- so a caller injecting a
        fault at an arbitrary instant must hold at a moment it has established rather
        than assume this succeeds. Raises KeyError if `code` names no station here.
        """
        machine = self._machines[code]
        machine.apply(Command.HOLD, reason)
        machine.settle()

    def unhold(self, code: str) -> None:
        """Release a `Held` station back to `Execute`.

        Raises ValueError if the station is not `Held`, KeyError if `code` names no
        station here.
        """
        machine = self._machines[code]
        machine.apply(Command.UNHOLD)
        machine.settle()

    def _source_and_sink(self, index: int) -> tuple[Buffer | None, Buffer | None]:
        upstream = self._buffers[index - 1] if index > 0 else None
        downstream = self._buffers[index] if index < len(self._buffers) else None
        return upstream, downstream

    async def step(self) -> CycleOutcome | None:
        """Pop the earliest due cycle and run it. None when the queue is empty.

        Must not be called concurrently with itself: one sequential driver, one queue.
        That is D1's whole premise -- it is what makes a run a function of the seed
        rather than of the event loop -- and it is also what the `assert acquired is
        not None` below rests on, since a station callback is awaited between the pool
        check and the acquire.
        """
        due = self._queue.pop()
        if due is None:
            return None
        at, index = due

        station = self._stations[index]
        machine = self._machines[station.code]
        upstream, downstream = self._source_and_sink(index)
        takt = station.next_takt()

        # A Held or Aborted station does not cycle and does not clear itself (§3.3).
        # It still occupies its slot in the queue, so that unholding it resumes
        # production without the line having to be rebuilt.
        if machine.state in (State.HELD, State.ABORTED):
            next_at = at + timedelta(seconds=takt)
            self._queue.schedule(next_at, index)
            return CycleOutcome(
                station.code, at, False, machine.state, machine.reason, next_at
            )

        reason = self._suspend_reason(upstream, downstream)
        if reason is not None:
            # The reason is latched at the moment of suspension and deliberately not
            # refreshed while the station stays Suspended -- which means a station can
            # hold a reason whose direction has since flipped -- observed as S1
            # reporting `starved:carrier-return` after the live condition had become
            # `blocked:B1_2`. This reads like it contradicts §5.4, which categorises a
            # propagation chain by direction, so: it is the causal reason and not the
            # instantaneous one, and the cause is what diagnosis is for. Nothing stale
            # reaches the database either -- `state_changes` gets no row for a change
            # within an episode, so the row the analysis reads was true when written.
            # Rare by construction, because downstream-first ordering normally clears
            # a station from below before its own condition can flip underneath it: at
            # carrier_count 12 it occurred once in 40,000 steps, and at the configured
            # 18 not at all, at takt jitter sigma 0.05 or 0.5.
            if machine.state is State.EXECUTE:
                machine.apply(Command.SUSPEND, reason)
                machine.settle()
                await station.publish_state(at, machine.state, machine.reason)
            next_at = at + timedelta(seconds=takt)
            self._queue.schedule(next_at, index)
            return CycleOutcome(
                station.code, at, False, machine.state, machine.reason, next_at
            )

        if machine.state is State.SUSPENDED:
            machine.apply(Command.UNSUSPEND)
            machine.settle()
            await station.publish_state(at, machine.state, machine.reason)

        if upstream is None:
            acquired = self._carriers.acquire()
            # _suspend_reason returned None, so the pool was not empty. An assert
            # rather than a cast: if this ever fires, the suspend rule and the
            # movement below have drifted apart, which is worth a crash.
            assert acquired is not None
            carrier = acquired
            self._parts[carrier.carrier_id] = PartState()
        else:
            carrier = upstream.take()

        part = self._parts[carrier.carrier_id]
        await station.run_cycle(at, carrier, part)

        if downstream is None:
            del self._parts[carrier.carrier_id]
            self._carriers.release(carrier)
        else:
            downstream.put(carrier)

        next_at = at + timedelta(seconds=takt)
        self._queue.schedule(next_at, index)
        return CycleOutcome(
            station.code, at, True, machine.state, machine.reason, next_at
        )

    def _suspend_reason(
        self, upstream: Buffer | None, downstream: Buffer | None
    ) -> SuspendReason | None:
        """§3.3's buffer rule, plus the one condition that is not a buffer.

        `upstream is None` is what identifies the head of the line, so no station
        index is needed: the same fact that says S1 has no feeding buffer is the fact
        that says the carrier pool is what feeds it.
        """
        if upstream is None and self._carriers.available == 0:
            return SuspendReason("starved", CARRIER_RETURN)
        return suspend_reason_for(upstream, downstream)
