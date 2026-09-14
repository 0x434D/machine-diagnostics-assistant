import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import new_server
from simulator.address_space import AddressSpace, build_address_space, publish_clock
from simulator.clock import Phase, SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.events import EVENT_FIELDS
from simulator.historian import Ledger, attach_historian


async def _build() -> AddressSpace:
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    return await build_address_space(server, idx, Settings().buffer_capacity)


@pytest_asyncio.fixture
async def space() -> AddressSpace:
    return await _build()


@pytest.mark.asyncio
async def test_the_tree_carries_exactly_twenty_five_historised_streams(
    space: AddressSpace,
) -> None:
    """The number Tasks 9 and 10's guards are sized against. §15 said nine; §4.1's
    own tree says this. If it moves, the page sizes and the truncation guard move
    with it."""
    streams = sum(len(nodes.historised) for nodes in space.stations.values())
    streams += sum(1 for _ in space.buffers)  # Level only
    assert streams == 25


@pytest.mark.asyncio
async def test_every_station_carries_packml_state_and_its_reason(
    space: AddressSpace,
) -> None:
    """§4.1 gives all four both. M1 omitted them deliberately, because a State node
    that never leaves Execute is a fake; the state machine arrived in Task 2, so they
    are real now."""
    for code, nodes in space.stations.items():
        assert "State" in nodes.historised, code
        assert "StateReason" in nodes.historised, code


@pytest.mark.asyncio
async def test_buffers_name_the_stations_they_sit_between(space: AddressSpace) -> None:
    """§4.1: the gateway reads these on connect and fills the buffers table, so the
    topology is discovered rather than configured."""
    between = {
        buffer_id: (
            await nodes.upstream.read_value(),
            await nodes.downstream.read_value(),
        )
        for buffer_id, nodes in space.buffers.items()
    }
    assert between == {
        "B1_2": ("S1_Feeding", "S2_Joining"),
        "B2_3": ("S2_Joining", "S3_Inspection"),
        "B3_4": ("S3_Inspection", "S4_Outfeed"),
    }


@pytest.mark.asyncio
async def test_m2b_nodes_are_absent_rather_than_holding_constants(
    space: AddressSpace,
) -> None:
    """§13's standard: nothing in a milestone is faked. Lots and assembly serials
    arrive in M2b with the data that makes them change."""
    s1 = space.stations["S1_Feeding"]
    assert "Lane1_Lot" not in s1.historised
    assert "CurrentAssemblySerial" not in s1.historised


@pytest.mark.asyncio
async def test_the_buffers_name_stations_that_exist(space: AddressSpace) -> None:
    """The whole of the discovered topology is the equality between these strings and
    the Stations folder's browse names. A typo on either side is a gateway that fills
    its buffers table with a foreign key pointing at nothing, and nothing in either
    stack would notice until the analysis asked which station feeds B2_3."""
    named = {
        name
        for nodes in space.buffers.values()
        for name in (
            await nodes.upstream.read_value(),
            await nodes.downstream.read_value(),
        )
    }
    assert named <= set(space.stations)


@pytest.mark.asyncio
async def test_every_station_code_has_its_own_configured_takt(
    space: AddressSpace,
) -> None:
    """§3.1: the stations do NOT share one takt, and `Station._nominal_takt` looks
    the number up by `StationNodes.code` -- these browse names. The lookup falls back
    to the line-wide takt for a station the configuration does not name, deliberately
    and silently (a station handed a takt of zero would wedge the queue), so a code
    that drifts from `station_takt_seconds`' keys produces a perfectly balanced line
    with no error anywhere: every buffer oscillates between empty and one, and buffer
    capacity bounds nothing. This is the only place both spellings are in scope."""
    assert set(space.stations) <= set(Settings().station_takt_seconds)


@pytest.mark.asyncio
async def test_topology_is_browsable_under_line_stations() -> None:
    """§4.1: the gateway discovers the line's topology by browsing, never by config."""
    space = await _build()
    stations = space.stations_folder
    found = [(await s.read_browse_name()).Name for s in await stations.get_children()]
    assert found == ["S1_Feeding", "S2_Joining", "S3_Inspection", "S4_Outfeed"]


@pytest.mark.asyncio
async def test_event_type_carries_an_image_field(space: AddressSpace) -> None:
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
async def test_custom_event_fields_decode_first_and_in_declared_order(
    space: AddressSpace,
) -> None:
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
async def test_only_the_inspection_station_can_trigger_an_event(
    space: AddressSpace,
) -> None:
    """§4.1 gives all four stations an event type; M2a builds the one whose payload
    exists. The other three must fail loudly rather than silently drop an event --
    Task 7 wires four stations through one Protocol, and three of them have no
    generator to trigger."""
    with pytest.raises(ValueError, match="emits no events"):
        await space.stations["S1_Feeding"].trigger_event(
            datetime(2026, 9, 13, tzinfo=UTC), {}
        )


@pytest.mark.asyncio
async def test_an_event_missing_a_field_is_refused(space: AddressSpace) -> None:
    """EventGenerator reuses one Event object across every trigger, so a field left
    out keeps the previous event's value and goes out as if it were this one's -- a
    defect class attributed to the wrong serial, with nothing raised."""
    with pytest.raises(ValueError, match="EVENT_FIELDS"):
        await space.stations["S3_Inspection"].trigger_event(
            datetime(2026, 9, 13, tzinfo=UTC),
            {"AssemblySerial": "A-00000001"},
        )


@pytest.mark.asyncio
async def test_clock_is_a_sibling_of_stations_with_exactly_three_variables() -> None:
    """§4.1: Clock sits under Line, next to Stations and Buffers -- not folded into
    either, since it describes the line's own timebase rather than something a
    station measures -- carrying exactly SimulatedTime, Phase and Speed, browsable by
    the same path a gateway walks to find Stations."""
    space = await _build()

    siblings = {
        (await c.read_browse_name()).Name for c in await space.line.get_children()
    }
    assert siblings == {"Clock", "Stations", "Buffers"}

    names = {
        (await c.read_browse_name()).Name for c in await space.clock.get_children()
    }
    assert names == {"SimulatedTime", "Phase", "Speed"}


@pytest.mark.asyncio
async def test_clock_nodes_track_simulated_clock_through_both_phases(
    space: AddressSpace,
) -> None:
    """Phase and Speed must agree with SimulatedClock.phase/catchup_speed exactly --
    not a second, independent notion of either computed here (see
    address_space.publish_clock and its _clock_snapshot helper). Speed is exactly
    1.0 once live, never the configured catchup_speed (§3.2)."""
    boot = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    wall = [boot]
    clock = SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=1), catchup_speed=600.0),
        wall_fn=lambda: wall[0],
    )

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
async def test_simulated_time_is_utc_aware_and_tracks_clock_now(
    space: AddressSpace,
) -> None:
    """§4.1: SimulatedTime is UTC-aware and matches SimulatedClock.now() to within
    the publish cadence -- the node and the clock it mirrors must never read as two
    different clocks."""
    # A negligible history depth, not the injected fake wall_fn test_clock.py uses:
    # this test wants the real wall clock actually advancing, so "within the update
    # interval" is a real bound rather than a tautology against a frozen one.
    clock = SimulatedClock(
        ClockConfig(history_depth=timedelta(milliseconds=1), catchup_speed=2.0)
    )
    interval = 0.05

    task = asyncio.create_task(publish_clock(space, clock, interval_seconds=interval))
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
    """Reconciliation covers the historised streams and nothing else (see
    build_address_space's comment on the Clock object). Historising the clock too
    would change those counts, which is exactly the defect this guards against: the
    historian's own handler set, not a count that could stay right by accident.

    M1's historian still attaches exactly S3's two variables and its event node --
    Task 7 raises that to §4.1's 25 when it deletes station_s3.py -- so the clock's
    absence is what this asserts, not the size of the set.
    """
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    space = await build_address_space(server, idx, Settings().buffer_capacity)

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
    assert {
        space.clock_time,
        space.clock_phase,
        space.clock_speed,
    } & historized == set()
