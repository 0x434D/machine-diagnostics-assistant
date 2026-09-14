"""D1: the line advances on a discrete-event queue, not on four concurrent loops.

§3.6 requires that the same seed plus the same scenario reproduces a run exactly.
Four `asyncio` tasks drawing from one RNG and racing on buffer levels cannot promise
that -- task interleaving orders the draws. A single queue popped in time order can,
and it handles independently jittered per-station takt naturally, which a fixed
global tick does not.

The queue also collapses catch-up and live into one mechanism: catch-up drains it as
fast as it can, live sleeps until each cycle's wall-clock equivalent (§4.3). That is
why `run_catchup` and `run_live` are here rather than in a driver of their own -- they
are the same loop over the same queue, differing only in what they wait for, and M1's
two separate generators are what made the seam between them a hole nothing could see.
"""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple, Protocol

from asyncua.server.history_sql import HistorySQLite

from simulator.buffers import Buffer, suspend_reason_for
from simulator.carriers import Carrier, CarrierPool
from simulator.clock import Phase, SimulatedClock
from simulator.config import Settings
from simulator.historian import LedgerWriter
from simulator.identity import Assembly
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


_BRING_UP: tuple[Command, ...] = (Command.CLEAR, Command.RESET, Command.START)
"""The commands that take a station from `Aborted` to `Execute`, in order.

`StateMachine` is what decides the states each one passes through, so this names the
path and not the result -- see `Line.bring_up`.
"""

BRING_UP_TRANSITIONS = len(_BRING_UP) * 2
"""How many `State` rows one station's bring-up publishes: every command is an acting
state and the waiting state it settles into, and each needs its own instant. Public
because the driver has to fit them all between the historian's priming row and the
first cycle."""


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
    """The assembly riding a carrier, and what the line has learned about it.

    Keyed by carrier id on the Line rather than stored in the buffers, because that
    is what a real line does -- the carrier has a tag and the part is whatever is
    currently on it. S1 creates the assembly and puts it here; S2 presses against its
    serial; S3 writes the disposition and the reason; S4 reads all three.

    **Three fields, and each is one an event needs.** `assembly` is §3.1's identity
    with its as-built components, and it is what makes S2's press and S4's sorting
    record the serial they actually handled rather than the one a later time-join
    would guess at -- §3.4a's rule. `disposition` and `reason` are §5.2's
    `part_dispositions` row, carried from the station that decided them to the station
    that publishes them. Nothing else belongs here: the process values are published
    where they are measured, and the Line never reads any of this.
    """

    assembly: Assembly | None = None
    disposition: str | None = None
    # The classifier's named reason for a reject, empty for a good part. A plain str
    # rather than `str | None`, because "no reason" and "not inspected yet" are not the
    # same state and `disposition is None` is already the second one.
    reason: str = ""


class BufferLevel(NamedTuple):
    buffer_id: str
    level: int


@dataclass(frozen=True)
class CycleOutcome:
    station_code: str
    at: datetime
    produced: bool
    state: State
    reason: str
    next_at: datetime
    moved: tuple[BufferLevel, ...] = ()
    """The buffers whose `Level` this cycle changed, and what each now holds.

    A cycle that produced moves one carrier: out of the buffer above and into the one
    below, each by exactly one, so this is the complete set of levels that changed and
    every entry in it is a genuine change. A cycle that did not produce moved nothing
    and carries none.

    Here rather than left to the driver to diff `buffers` against its own copy: the
    Line owns movement, and a second party watching for changes would be the same rule
    written twice. It is also what keeps §4.1's three `Level` streams from being
    republished on every one of the ~80,000 cycles a 33 h catch-up runs -- four fifths
    of which would be a value the historian discards anyway.
    """


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

    @property
    def next_due(self) -> datetime | None:
        """When the earliest queued cycle is due, without popping it.

        Catch-up needs it to stop *before* stepping past its horizon -- `step` has
        already written the cycle by the time an outcome exists -- and live needs it to
        sleep until the next event rather than until the cycle it just ran comes round
        again, which with four different takts is a different instant.
        """
        return self._heap[0][0] if self._heap else None

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
        transition_interval: timedelta,
    ) -> None:
        """`transition_interval` is how far apart two PackML states published for the
        same station are placed on the simulated timeline.

        It is not cosmetic and it has no default. §5.2 keys `state_changes` on
        `(station_id, source_ts)`, so two states published at one instant are one row
        in Postgres and the second silently replaces the first: a bring-up would arrive
        as `Execute` alone and a hold as `Held` alone, with the transitions that led
        there gone. `Settings.state_transition_seconds` is the configured value.
        """
        if len(buffers) != len(stations) - 1:
            raise ValueError(
                f"{len(stations)} stations need {len(stations) - 1} buffers between "
                f"them, got {len(buffers)}"
            )
        for index, buffer in enumerate(buffers):
            between = (stations[index].code, stations[index + 1].code)
            if between != (buffer.upstream, buffer.downstream):
                # A buffer's own `upstream`/`downstream` are what §4.1 publishes and
                # what the gateway browses to build its topology, while the Line
                # decides which stations it actually sits between by position. Nothing
                # else compares the two, and a line that disagrees with the tree it
                # serves answers every propagation question about the wrong pair of
                # stations -- consistently, and with no error anywhere.
                raise ValueError(
                    f"{buffer.buffer_id} names ({buffer.upstream}, "
                    f"{buffer.downstream}) but this line puts it between {between[0]} "
                    f"and {between[1]}"
                )
        self._stations = list(stations)
        self._buffers = list(buffers)
        self._carriers = carriers
        self._transition = transition_interval
        self._queue = CycleQueue()
        # Carrier id -> the part currently riding it. A carrier holds at most one
        # part, so the id is a sufficient key and no buffer has to carry a payload.
        self._parts: dict[int, PartState] = {}
        # Every station starts Aborted, which is where a machine that has never been
        # cleared genuinely is. `bring_up` is what walks them to Execute, and it is
        # async because each of those transitions is published: a line is constructed
        # here and started there. A Line that is never brought up therefore produces
        # nothing -- `step` already declines to cycle an Aborted station -- which is
        # the visible failure, not a silent one.
        self._machines = {station.code: StateMachine() for station in stations}

    @property
    def buffers(self) -> Sequence[Buffer]:
        return self._buffers

    @property
    def next_due(self) -> datetime | None:
        """The instant the next cycle is due, or None once the queue is empty."""
        return self._queue.next_due

    @property
    def station_states(self) -> dict[str, tuple[State, str]]:
        """Every station's PackML state and its reason, in line order.

        For `simulator.status`, which is how the demo and Task 11's HMI fallback see a
        line that has starved without an OPC UA client at hand. Read-only: the state
        machines themselves stay private, because everything that may drive one goes
        through `bring_up`, `hold`, `unhold` or a cycle -- and each of those publishes
        what it did.
        """
        return {
            code: (machine.state, machine.reason)
            for code, machine in self._machines.items()
        }

    def machine_for(self, code: str) -> StateMachine:
        return self._machines[code]

    def seed(self, start_ts: datetime) -> None:
        """Schedule every station's first cycle at the same instant. Downstream-first
        ordering is what turns that into a line starting up rather than a part
        teleporting."""
        for index in range(len(self._stations)):
            self._queue.schedule(start_ts, index)

    async def _drive(
        self,
        code: str,
        at: datetime,
        command: Command,
        reason: SuspendReason | str | None = None,
    ) -> datetime:
        """Apply one PackML command and publish both states it passes through.

        Returns the instant after the last one published, so a caller sequencing
        several commands carries the cursor forward rather than recomputing it.

        Both states are published, not just the settled one: `Holding` is the
        transition and `Held` is the condition, and an analysis that only ever sees
        `Held` cannot tell a station that was commanded to hold from one that was found
        holding. They go one `transition_interval` apart because §5.2 keys
        `state_changes` on `(station_id, source_ts)` and would otherwise keep only the
        second.

        The harder half of the same rule, and why `step` drives its suspensions through
        here rather than applying them itself: `state_changes` records
        `from_state`/`to_state`, and `Execute -> Suspended` is not an edge PackML has.
        Publishing only the settled state put thousands of pairs in that table that the
        state machine cannot produce -- the same defect `bring_up` refuses in larger
        print, one file apart.
        """
        station = self._station_for(code)
        machine = self._machines[code]
        machine.apply(command, reason)
        await station.publish_state(at, machine.state, machine.reason)
        machine.settle()
        await station.publish_state(
            at + self._transition, machine.state, machine.reason
        )
        return at + 2 * self._transition

    def _station_for(self, code: str) -> StationCycle:
        """Raises KeyError for a code this line does not carry, like `_machines`."""
        for station in self._stations:
            if station.code == code:
                return station
        raise KeyError(code)

    async def bring_up(self, first_at: datetime) -> datetime:
        """Start every station: Aborted -> Clearing -> Stopped -> Resetting -> Idle ->
        Starting -> Execute, publishing all six transitions. Returns the instant after
        the last one.

        Published rather than performed silently, and as the real sequence rather than
        a jump. Before this existed the line walked its machines to `Execute` inside
        `__init__` and told no one: the historian's first `State` row for a station was
        whatever its first *suspension* wrote, so S1_Feeding -- which starts with an
        empty B1_2 in front of it and runs unimpeded -- read `Aborted` with no reason
        for the first 894.9 simulated seconds of a shipped-settings run, ~157 parts,
        while `PartCount` climbed beside it. "Was S1 running 300 s in?" was answered
        `Aborted`, and both the ledger and the historian agreed, because the two were
        wrong together. That is the quiet wrong answer this system exists not to give.

        A jump would be the same defect in smaller print: PackML has no Aborted ->
        Execute transition, and `state_changes` records `from_state`/`to_state`, so an
        illegal pair in the record is worse than the gap it replaces. The commands below
        are the ones `StateMachine` accepts from each state, so the sequence is the
        machine's, not a script that happens to agree with it today.

        `first_at` must sit after the historian's priming row (which puts `Aborted` on
        record) and before the first cycle. `run_catchup` places the window.
        """
        last = first_at
        for code in self._machines:
            at = first_at
            for command in _BRING_UP:
                at = await self._drive(code, at, command)
            last = max(last, at)
        return last

    async def hold(self, code: str, at: datetime, reason: str) -> None:
        """Put a station into `Held` -- §3.3's cause candidate, which does not clear
        itself. M2c injects faults through this; M2a's propagation proof uses it to
        stop S2 and watch S3 starve.

        Publishes `Holding` then `Held`, both carrying `reason` (§3.3 makes the field
        non-optional), one `transition_interval` apart.

        Raises ValueError if the station is not in `Execute`, which is the only state
        PackML accepts Hold from. A running station leaves `Execute` whenever its
        buffers suspend it -- a couple of percent of steps -- so a caller injecting a
        fault at an arbitrary instant must hold at a moment it has established rather
        than assume this succeeds. Raises KeyError if `code` names no station here.
        """
        await self._drive(code, at, Command.HOLD, reason)

    async def unhold(self, code: str, at: datetime) -> None:
        """Release a `Held` station back to `Execute`, publishing `Unholding` then
        `Execute`.

        Raises ValueError if the station is not `Held`, KeyError if `code` names no
        station here.
        """
        await self._drive(code, at, Command.UNHOLD)

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
                # Through _drive, so `Suspending` reaches the historian alongside
                # `Suspended` and the recorded edge is one PackML has. Its two
                # publishes are `state_transition_seconds` apart and both land inside
                # this cycle, which run_catchup's window check keeps under one takt.
                await self._drive(station.code, at, Command.SUSPEND, reason)
            next_at = at + timedelta(seconds=takt)
            self._queue.schedule(next_at, index)
            return CycleOutcome(
                station.code, at, False, machine.state, machine.reason, next_at
            )

        if machine.state is State.SUSPENDED:
            # `Unsuspending` for the same reason: `Suspended -> Execute` is not an edge
            # either. The cycle below still runs at `at` while the settled `Execute` is
            # stamped one transition later -- the station is producing again the moment
            # its buffer cleared, and the half-second is the transition it takes to say
            # so, not a delay in the line.
            await self._drive(station.code, at, Command.UNSUSPEND)

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
            station.code,
            at,
            True,
            machine.state,
            machine.reason,
            next_at,
            # Read after the movement above, so these are the levels the buffers now
            # hold rather than the ones they held when the cycle started.
            tuple(
                BufferLevel(buffer.buffer_id, buffer.level)
                for buffer in (upstream, downstream)
                if buffer is not None
            ),
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


async def _publish_levels(writer: LedgerWriter, outcome: CycleOutcome) -> None:
    """§4.1's `Level` streams, for the buffers this cycle actually moved a carrier
    through. See `CycleOutcome.moved`."""
    for buffer_id, level in outcome.moved:
        await writer.write_level(buffer_id, outcome.at, level)


async def run_catchup(
    line: Line,
    writer: LedgerWriter,
    clock: SimulatedClock,
    settings: Settings,
    storage: HistorySQLite,
) -> datetime:
    """Build the configured depth of history in process (§3.2), returning the simulated
    instant production resumes at.

    That instant is the queue's own next due cycle, returned rather than recomputed
    from `history_start + history_depth`: recomputing would be wrong by the cumulative
    takt jitter -- several seconds over 33 h -- and would leave a hole nothing above
    can see. `run_live` does not consume it, because the queue already carries the
    continuation; `server.main` logs it, which is how a seam that opened anyway would
    be visible.

    The horizon is checked *before* stepping rather than on the outcome: `Line.step`
    has already run the cycle and written its rows by the time it returns one, so
    breaking on `outcome.at >= horizon` writes one cycle of history past the depth that
    was asked for and then reports a resume instant before it.

    Paced in batches of `settings.catchup_batch_size` cycles, sleeping
    `settings.catchup_batch_pause_seconds` between them: asyncua's per-monitored-item
    notification queue caps at 10,000 and silently discards the *oldest* entry past
    that, so nothing here may run the whole depth in one uninterrupted burst. M1
    measured 19,800 parts losing the first 9,800 rows per stream with no pacing at all;
    Task 1 (R5) re-measured the batch size that holds at 25 streams -- 500 parts and
    0.05 s, 495,000 rows written with none dropped -- and those two numbers are
    `settings.catchup_batch_size` and `settings.catchup_batch_pause_seconds`. They are
    measured, not guessed.

    Starts the line before generating anything, and the six PackML transitions that
    takes are published on the simulated timeline immediately before `history_start`
    (`Line.bring_up` has what that is worth). The window has to fit between the
    historian's priming row -- one takt before `history_start`, which is where `Aborted`
    is on record -- and the first cycle at `history_start`, so a station's own history
    reads Aborted, the real sequence, Execute, then whatever production did. Publishing
    at or after `history_start` would collide with the first cycle's own suspensions,
    which §5.2's `(station_id, source_ts)` key would resolve by keeping one of them.

    Reads the historian back through `storage` before returning and raises if it
    disagrees with the ledger (`LedgerWriter.reconcile`), so a caller that gets a
    normal return has R1's guarantee already checked rather than attempted.

    Raises ValueError if the configured bring-up window does not fit in that gap.
    """
    interval = timedelta(seconds=settings.state_transition_seconds)
    window = BRING_UP_TRANSITIONS * interval
    if window >= timedelta(seconds=settings.takt_seconds):
        raise ValueError(
            f"bring-up needs {BRING_UP_TRANSITIONS} x "
            f"{settings.state_transition_seconds} s = {window.total_seconds()} s of "
            f"simulated timeline, and it has to fit between the historian's priming "
            f"row (one takt, {settings.takt_seconds} s, before history_start) and the "
            "first cycle. Lower state_transition_seconds or raise takt_seconds"
        )
    await line.bring_up(clock.history_start - window)

    line.seed(clock.history_start)
    horizon = clock.history_start + clock.history_depth
    cycles = 0

    while (due := line.next_due) is not None and due < horizon:
        outcome = await line.step()
        if outcome is not None:
            await _publish_levels(writer, outcome)
        cycles += 1
        if cycles % settings.catchup_batch_size == 0:
            await asyncio.sleep(settings.catchup_batch_pause_seconds)

    await writer.reconcile(storage)

    resume_at = line.next_due
    if resume_at is None:
        raise RuntimeError(
            "the cycle queue emptied during catch-up: every station reschedules "
            "itself, so an empty queue means the line was built with no stations"
        )
    return resume_at


async def run_live(
    line: Line,
    writer: LedgerWriter,
    clock: SimulatedClock,
    settings: Settings,
) -> None:
    """Live production, on the queue catch-up left behind (§3.2, §4.3).

    There is no second seeding and no instant to resume from. The queue already holds
    each station's next cycle, at its own takt and its own phase, and `Line.seed`
    pushes rather than replaces -- re-seeding here would schedule all four stations a
    second time, so every station would cycle twice per takt and the heap would grow
    for as long as the plant ran.

    Sleeps until the next queued cycle is due *in simulated time* rather than stamping
    each part with `clock.now()` and sleeping a takt. The two agree once simulated time
    has caught up with the wall clock, and they differ by exactly the defect this
    closes: catch-up fills the timeline up to the clock's boot instant in far less wall
    time than the run takes to reach Phase.LIVE, so a first live part stamped
    `clock.now()` would leave the whole interval between the two -- every second of
    catch-up wall time, tens of parts at the configured takt -- as a hole in the
    simulated timeline that no layer above can see. Sleeping to the due instant makes
    that sleep simply zero until the backlog is emitted, so the seam closes at
    generation speed and pacing returns to 1.0 by itself. Once it has, the sleep *is*
    the interval the station wrote to TaktTime, which is what keeps that number a true
    claim about how long the cycle took rather than one nothing waits out. The backlog
    is bounded by whichever of the catch-up wall and the generation wall is longer
    (170 s and 151 s measured at M1's defaults, so tens of cycles), far below the
    10,000-entry notification queue cap catch-up has to pace against.

    M1's `run_live` drew its takt jitter from a distinguishing `seed ^ 1` stream so
    that changing the history depth could not shift what live produced. That property
    does not survive the queue and is not worth faking: catch-up and live are now one
    continuous run of one line, so the depth decides the part counts, the buffer levels
    and which carrier is where when live begins, whatever any RNG does. §3.6's actual
    claim -- the same seed and the same scenario reproduce a run exactly -- is what the
    single queue and the per-station RNGs give, and it still holds.
    """
    while True:
        if clock.phase is not Phase.LIVE:
            await asyncio.sleep(settings.takt_seconds)
            continue
        outcome = await line.step()
        if outcome is None:
            return
        await _publish_levels(writer, outcome)
        due = line.next_due
        if due is None:
            return
        await asyncio.sleep(max(0.0, (due - clock.now()).total_seconds()))
