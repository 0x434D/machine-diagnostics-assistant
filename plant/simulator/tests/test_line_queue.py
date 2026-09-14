"""D1's discrete-event queue, and the Line that runs on it.

The Line is exercised with a recording fake rather than the real stations (conftest's
`FakeStation`): Task 5's stations do I/O, and the behaviour under test here --
ordering, buffer movement, suspension -- is entirely the Line's.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import T0, TRANSITION, FakeStation, build_fake_line, fake_line
from simulator.buffers import Buffer
from simulator.carriers import CarrierPool
from simulator.line import BRING_UP_TRANSITIONS, CycleQueue, Line
from simulator.packml import State


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
    line, stations = await build_fake_line()
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
    line, stations = await build_fake_line()
    await line.step()  # S4 first, and B3_4 is empty
    assert stations[3].states[-1][1] is State.SUSPENDED
    assert stations[3].states[-1][2] == "starved:B3_4"


@pytest.mark.asyncio
async def test_a_station_that_gets_its_buffer_back_resumes() -> None:
    line, _ = await build_fake_line()
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
    line, _ = await build_fake_line(carriers=12, capacity=5)
    await line.hold("S4", T0, "test jam")
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
        line, _ = await build_fake_line()
        trace: list[tuple[str, datetime, bool]] = []
        for _ in range(64):
            outcome = await line.step()
            assert outcome is not None
            trace.append((outcome.station_code, outcome.at, outcome.produced))
        return trace

    assert await run() == await run()


def test_a_buffer_placed_between_stations_it_does_not_name_is_refused() -> None:
    """A buffer publishes the two stations it sits between (§4.1) and the Line decides
    where it really sits, by position. Nothing else compares the two, and a line that
    disagrees with the tree it serves answers every propagation question about the
    wrong pair of stations -- consistently, and with no error anywhere."""
    stations = [FakeStation(code) for code in ("S1", "S2", "S3", "S4")]
    buffers = [
        Buffer("B1_2", 5, "S1", "S2"),
        Buffer("B2_3", 5, "S3", "S4"),  # transposed with B3_4
        Buffer("B3_4", 5, "S2", "S3"),
    ]
    with pytest.raises(ValueError, match="B2_3 names"):
        Line(
            stations=stations,
            buffers=buffers,
            carriers=CarrierPool(12),
            transition_interval=TRANSITION,
        )


def test_the_queue_reports_what_is_due_next_without_popping_it() -> None:
    """Catch-up stops on this rather than on the outcome of a step, because a step has
    already written its cycle by the time it returns one."""
    queue = CycleQueue()
    assert queue.next_due is None
    queue.schedule(T0 + timedelta(seconds=6), 0)
    queue.schedule(T0, 1)
    assert queue.next_due == T0
    assert len(queue) == 2
    assert queue.pop() == (T0, 1)
    assert queue.next_due == T0 + timedelta(seconds=6)


@pytest.mark.asyncio
async def test_a_produced_cycle_reports_the_levels_it_changed() -> None:
    """The driver publishes §4.1's Level streams from this, so a cycle that moved a
    carrier must name both buffers it moved it between -- and one that moved nothing
    must name none, or the historian gets a row for a level that did not change."""
    line, _ = await build_fake_line()
    first = await line.step()  # S4, starved
    assert first is not None
    assert first.produced is False
    assert first.moved == ()

    while True:
        outcome = await line.step()
        assert outcome is not None
        if outcome.station_code == "S2" and outcome.produced:
            break
    # S2 takes from B1_2 and puts into B2_3, each by exactly one.
    assert outcome.moved == (("B1_2", 0), ("B2_3", 1))


@pytest.mark.asyncio
async def test_bring_up_publishes_the_whole_packml_sequence() -> None:
    """A station's own history has to say how it got to Execute. PackML has no
    Aborted -> Execute transition, and `state_changes` records from_state/to_state, so
    a jump would put an illegal pair in the record rather than close the gap."""
    line, stations = fake_line()
    first = T0 - BRING_UP_TRANSITIONS * TRANSITION

    after = await line.bring_up(first)

    for station in stations:
        assert [state for _, state, _ in station.states] == [
            State.CLEARING,
            State.STOPPED,
            State.RESETTING,
            State.IDLE,
            State.STARTING,
            State.EXECUTE,
        ], station.code
        # Distinct instants, in order: §5.2 keys state_changes on
        # (station_id, source_ts), so a shared instant is one row in Postgres and five
        # of these six would simply not exist downstream.
        stamps = [at for at, _, _ in station.states]
        assert stamps == sorted(set(stamps))
        assert stamps[0] == first
        assert stamps[-1] < T0
    assert after == first + BRING_UP_TRANSITIONS * TRANSITION
    assert all(reason == "" for _, _, reason in stations[0].states)


@pytest.mark.asyncio
async def test_a_line_that_was_never_brought_up_produces_nothing() -> None:
    """Aborted is where a machine that has never been cleared genuinely is, and an
    Aborted station does not cycle (§3.3). The failure of a driver that forgets to
    start the line is therefore a line that visibly makes nothing, not one that
    produces while its published state says otherwise."""
    line, stations = fake_line()
    line.seed(T0)

    for _ in range(16):
        outcome = await line.step()
        assert outcome is not None
        assert outcome.produced is False
        assert outcome.state is State.ABORTED
    assert all(not station.cycles for station in stations)


@pytest.mark.asyncio
async def test_holding_a_station_publishes_holding_then_held() -> None:
    """Task 12 holds S2 and asserts what follows downstream. A hold that reached
    neither OPC UA nor Postgres would make the proof's cause invisible -- and Held is
    §3.3's cause candidate, the one state M3's diagnosis has to be able to name."""
    line, stations = await build_fake_line()
    held_at = T0 + timedelta(seconds=60)

    await line.hold("S2", held_at, "test jam")

    assert stations[1].states[-2] == (held_at, State.HOLDING, "test jam")
    assert stations[1].states[-1] == (held_at + TRANSITION, State.HELD, "test jam")

    await line.unhold("S2", held_at + timedelta(seconds=120))
    assert [state for _, state, _ in stations[1].states[-2:]] == [
        State.UNHOLDING,
        State.EXECUTE,
    ]
