import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import new_server
from simulator.address_space import build_address_space, publish_clock
from simulator.clock import Phase, SimulatedClock
from simulator.config import ClockConfig
from simulator.events import EVENT_FIELDS
from simulator.historian import Ledger, attach_historian


@pytest.mark.asyncio
async def test_s3_exposes_exactly_the_two_signals_and_no_packml() -> None:
    """§4.1 lists State and StateReason for every station, but §13 puts PackML in M2.
    A node that never changes is a fake, and §13's standard for M1 is that nothing
    in it is faked -- so they are absent, not static."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    names = {(await c.read_browse_name()).Name for c in await space.s3.get_children()}
    assert "TaktTime" in names
    assert "PartCount" in names
    assert "State" not in names
    assert "StateReason" not in names


@pytest.mark.asyncio
async def test_topology_is_browsable_under_line_stations() -> None:
    """§4.1: the gateway discovers the line's topology by browsing, never by config."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    await build_address_space(server, idx)

    line = await server.nodes.objects.get_child([f"{idx}:Line"])
    stations = await line.get_child([f"{idx}:Stations"])
    found = [(await s.read_browse_name()).Name for s in await stations.get_children()]
    assert found == ["S3_Inspection"]


@pytest.mark.asyncio
async def test_event_type_carries_an_image_field() -> None:
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    props = {
        (await p.read_browse_name()).Name
        for p in await space.event_type.get_properties()
    }
    assert {
        "AssemblySerial",
        "Disposition",
        "DefectClass",
        "Confidence",
        "Image",
    } <= props


@pytest.mark.asyncio
async def test_custom_event_fields_decode_first_and_in_declared_order() -> None:
    """Nothing pins EVENT_FIELDS' order against how the server actually hands the
    properties back -- reordering it would silently mis-assign every column
    Tasks 8 and 10 decode by position. get_properties() also returns
    BaseEventType's own inherited fields (13 of them); this only pins that our six
    lead, in the order EVENT_FIELDS declares.

    Asserted against a literal name list, not [name for name, _ in EVENT_FIELDS]:
    comparing the server's order against a list *derived from EVENT_FIELDS itself*
    is tautological -- swap two entries in EVENT_FIELDS and both sides move
    together, so the assertion still passes. A literal here is the only way a
    reorder of EVENT_FIELDS actually fails this test.
    """
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    names = [
        (await p.read_browse_name()).Name
        for p in await space.event_type.get_properties()
    ]
    assert names[: len(EVENT_FIELDS)] == [
        "AssemblySerial",
        "Disposition",
        "DefectClass",
        "Confidence",
        "ModelVersion",
        "Image",
    ]


@pytest.mark.asyncio
async def test_clock_is_a_sibling_of_stations_with_exactly_three_variables() -> None:
    """§4.1: Clock sits under Line, next to Stations -- not folded into it, since it
    describes the line's own timebase rather than something a station measures --
    carrying exactly SimulatedTime, Phase and Speed, browsable by the same path a
    gateway walks to find Stations."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    await build_address_space(server, idx)

    line = await server.nodes.objects.get_child([f"{idx}:Line"])
    siblings = {(await c.read_browse_name()).Name for c in await line.get_children()}
    assert siblings == {"Clock", "Stations"}

    clock = await line.get_child([f"{idx}:Clock"])
    names = {(await c.read_browse_name()).Name for c in await clock.get_children()}
    assert names == {"SimulatedTime", "Phase", "Speed"}


@pytest.mark.asyncio
async def test_clock_nodes_track_simulated_clock_through_both_phases() -> None:
    """Phase and Speed must agree with SimulatedClock.phase/catchup_speed exactly --
    not a second, independent notion of either computed here (see
    address_space.publish_clock and its _clock_snapshot helper). Speed is exactly
    1.0 once live, never the configured catchup_speed (§3.2)."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    boot = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    wall = [boot]
    clock = SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=1), catchup_speed=600.0),
        wall_fn=lambda: wall[0],
    )

    async with server:
        task = asyncio.create_task(publish_clock(space, clock, interval_seconds=0.01))
        try:
            await asyncio.sleep(0.05)
            assert await space.clock_phase.read_value() == Phase.CATCHUP.value
            assert await space.clock_speed.read_value() == 600.0

            wall[0] = boot + clock.catchup_duration + timedelta(seconds=1)
            await asyncio.sleep(0.05)
            assert await space.clock_phase.read_value() == Phase.LIVE.value
            # Exactly 1.0, not approximately -- the same pass condition
            # test_clock.py already pins for SimulatedClock.now() itself.
            assert await space.clock_speed.read_value() == 1.0
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_simulated_time_is_utc_aware_and_tracks_clock_now() -> None:
    """§4.1: SimulatedTime is UTC-aware and matches SimulatedClock.now() to within
    the publish cadence -- the node and the clock it mirrors must never read as two
    different clocks."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    # A negligible history depth, not the injected fake wall_fn test_clock.py uses:
    # this test wants the real wall clock actually advancing, so "within the update
    # interval" is a real bound rather than a tautology against a frozen one.
    clock = SimulatedClock(
        ClockConfig(history_depth=timedelta(milliseconds=1), catchup_speed=2.0)
    )
    interval = 0.05

    async with server:
        task = asyncio.create_task(
            publish_clock(space, clock, interval_seconds=interval)
        )
        try:
            await asyncio.sleep(interval * 3)
            simulated_time = await space.clock_time.read_value()
            assert simulated_time.tzinfo is not None
            assert simulated_time.utcoffset() == timedelta(0)
            drift = abs((clock.now() - simulated_time).total_seconds())
            assert drift <= interval * 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_clock_nodes_are_not_historized(tmp_path: Path) -> None:
    """R1 reconciles exactly three historised streams -- TaktTime, PartCount, the
    inspection event (see build_address_space's comment on the Clock object).
    Historising the clock too would change those counts, which is exactly the
    defect this guards against: the historian's own handler set, not a count that
    could stay right by accident."""
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx)

    ledger = Ledger()
    await attach_historian(
        server,
        space,
        tmp_path / "h.db",
        1000,
        datetime(2026, 9, 12, tzinfo=UTC),
        ledger,
    )

    historized = set(server.iserver.history_manager._handlers)
    assert historized == {space.takt, space.part_count, space.s3}
