"""D1's discrete-event queue, and the Line that runs on it.

The Line is exercised with a recording fake rather than the real stations: Task 5's
stations do I/O, and the behaviour under test here -- ordering, buffer movement,
suspension -- is entirely the Line's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from simulator.buffers import Buffer
from simulator.carriers import Carrier, CarrierPool
from simulator.line import CycleQueue, Line, PartState
from simulator.packml import State

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


@dataclass
class FakeStation:
    """Records what the Line asked it to do. Not a second implementation of
    StationCycle in the sense CLAUDE.md forbids -- a test double."""

    code: str
    takt: float = 6.0
    cycles: list[tuple[datetime, int]] = field(default_factory=list)
    states: list[tuple[datetime, State, str]] = field(default_factory=list)

    async def run_cycle(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        # Stamping the part is what a real station does with it -- S3 writes the
        # disposition and S4 reads it -- and it is what proves the Line handed over a
        # PartState rather than None.
        part.disposition = self.code
        self.cycles.append((at, carrier.carrier_id))

    async def publish_state(self, at: datetime, state: State, reason: str) -> None:
        self.states.append((at, state, reason))

    def next_takt(self) -> float:
        return self.takt


def build_line(carriers: int = 12, capacity: int = 5) -> tuple[Line, list[FakeStation]]:
    stations = [FakeStation(code) for code in ("S1", "S2", "S3", "S4")]
    buffers = [
        Buffer("B1_2", capacity, "S1", "S2"),
        Buffer("B2_3", capacity, "S2", "S3"),
        Buffer("B3_4", capacity, "S3", "S4"),
    ]
    line = Line(stations=stations, buffers=buffers, carriers=CarrierPool(carriers))
    line.seed(T0)
    return line, stations


def test_the_queue_pops_in_time_order() -> None:
    queue = CycleQueue()
    queue.schedule(T0 + timedelta(seconds=12), 0)
    queue.schedule(T0, 0)
    queue.schedule(T0 + timedelta(seconds=6), 0)
    assert [queue.pop(), queue.pop(), queue.pop()] == [
        (T0, 0),
        (T0 + timedelta(seconds=6), 0),
        (T0 + timedelta(seconds=12), 0),
    ]


def test_stations_scheduled_at_the_same_instant_run_downstream_first() -> None:
    """The decision that makes this a conveyor. Upstream-first would let one part
    traverse all four stations in zero simulated time."""
    queue = CycleQueue()
    for index in (0, 1, 2, 3):
        queue.schedule(T0, index)
    assert [queue.pop(), queue.pop(), queue.pop(), queue.pop()] == [
        (T0, 3),
        (T0, 2),
        (T0, 1),
        (T0, 0),
    ]


def test_an_empty_queue_pops_none() -> None:
    assert CycleQueue().pop() is None


@pytest.mark.asyncio
async def test_a_part_advances_exactly_one_station_per_takt() -> None:
    """The observable consequence of downstream-first ordering."""
    line, stations = build_line()
    for _ in range(16):  # four instants, four stations each
        await line.step()

    assert [at for at, _ in stations[0].cycles] == [
        T0,
        T0 + timedelta(seconds=6),
        T0 + timedelta(seconds=12),
        T0 + timedelta(seconds=18),
    ]
    # S2 cannot run at T0 -- B1_2 is empty until S1 has loaded into it.
    assert stations[1].cycles[0][0] == T0 + timedelta(seconds=6)
    assert stations[2].cycles[0][0] == T0 + timedelta(seconds=12)
    assert stations[3].cycles[0][0] == T0 + timedelta(seconds=18)


@pytest.mark.asyncio
async def test_a_starved_station_says_which_buffer_starved_it() -> None:
    line, stations = build_line()
    await line.step()  # S4 first, and B3_4 is empty
    assert stations[3].states[-1][1] is State.SUSPENDED
    assert stations[3].states[-1][2] == "starved:B3_4"


@pytest.mark.asyncio
async def test_a_station_that_gets_its_buffer_back_resumes() -> None:
    line, _ = build_line()
    for _ in range(24):
        await line.step()
    assert line.machine_for("S4").state is State.EXECUTE
    assert line.machine_for("S4").reason == ""


@pytest.mark.asyncio
async def test_carriers_run_out_before_the_first_buffer_fills() -> None:
    """A deliberately undersized pool -- twelve carriers against fifteen buffer slots,
    not the line's configured `carrier_count` -- so that stopping S4 exhausts it. The
    carriers then park downstream (B3_4 and B2_3 take five each, B1_2 the remaining
    two), so S1 suspends for want of a carrier while B1_2 still has room. An empty
    pool and a full B1_2 are different faults with different fixes, and this is the
    case that proves S1 can tell which one it hit."""
    line, _ = build_line(carriers=12, capacity=5)
    line.hold("S4", "test jam")
    for _ in range(400):
        await line.step()

    assert line.machine_for("S1").state is State.SUSPENDED
    assert line.machine_for("S1").reason == "starved:carrier-return"
    assert line.buffers[0].level < line.buffers[0].capacity


@pytest.mark.asyncio
async def test_the_same_seed_produces_the_same_run() -> None:
    """§3.6. The queue is the only thing deciding order, so this is the property that
    would break the moment station loops became concurrent."""

    async def run() -> list[tuple[str, datetime, bool]]:
        line, _ = build_line()
        trace: list[tuple[str, datetime, bool]] = []
        for _ in range(64):
            outcome = await line.step()
            assert outcome is not None
            trace.append((outcome.station_code, outcome.at, outcome.produced))
        return trace

    assert await run() == await run()
