import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from asyncua import Node, Server
from conftest import new_server
from simulator.address_space import (
    AddressSpace,
    build_address_space,
    historised_streams,
    publish_clock,
)
from simulator.clock import Phase, SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.events import (
    ALARM,
    ASSEMBLY_CREATED,
    COMPONENT_READ,
    INSPECTION_RESULT,
    PART_COMPLETED,
    PART_PROCESSED,
    EventType,
    event_types_for,
)
from simulator.historian import Ledger, _table_name, attach_historian

# §4.1's tree as a reader of the spec would write it down, kept apart from the tables
# that build it: asserting the served tree against address_space.STATION_SIGNALS would
# compare the code to itself and pass through any edit to it.
_HISTORISED_VARIABLES = {
    "S1_Feeding": {
        "State",
        "StateReason",
        "TaktTime",
        "LaneFill_1",
        "LaneFill_2",
        "PartCount",
    },
    "S2_Joining": {
        "State",
        "StateReason",
        "TaktTime",
        "JoiningForcePeak",
        "JoiningDistance",
        "PartCount",
    },
    "S3_Inspection": {"State", "StateReason", "TaktTime", "PartCount"},
    "S4_Outfeed": {
        "State",
        "StateReason",
        "TaktTime",
        "OutfeedFill",
        "GoodCount",
        "RejectCount",
    },
}

_LIVE_VARIABLES = {
    "S1_Feeding": {"Lane1_Lot", "Lane2_Lot", "CurrentAssemblySerial"},
    "S2_Joining": set[str](),
    "S3_Inspection": set[str](),
    "S4_Outfeed": set[str](),
}
"""D12's three, written down from §4.1 rather than read off STATION_LIVE_SIGNALS, for
the same reason the historised table above is."""

_BUFFER_VARIABLES = {"Level", "Capacity", "UpstreamStation", "DownstreamStation"}

_EVENT_FIELD_ORDER: dict[EventType, list[str]] = {
    COMPONENT_READ: ["ComponentSerial", "Lane", "LotCode", "Supplier"],
    ASSEMBLY_CREATED: ["AssemblySerial", "ComponentSerials", "CarrierId"],
    PART_PROCESSED: ["AssemblySerial", "Curve", "PeakForce", "JoiningDistance"],
    INSPECTION_RESULT: [
        "AssemblySerial",
        "CarrierId",
        "Disposition",
        "DefectClasses",
        "Confidences",
        "Confidence",
        "ModelVersion",
        "Image",
    ],
    PART_COMPLETED: ["AssemblySerial", "Disposition", "Reason"],
    ALARM: [
        "AlarmCode",
        "AlarmText",
        "AlarmSeverity",
        "AlarmRaisedAt",
        "AlarmActive",
        "AlarmAcknowledged",
    ],
}
"""The wire format, as literals.

Written out rather than derived from each `EventType.field_names`: comparing the
server's order against a list built from the table that produced it is tautological --
swap two entries and both sides move together. A literal here is the only way a
reorder actually fails a test, and the order is what every positional decode
downstream depends on.
"""


async def _build() -> tuple[Server, AddressSpace]:
    server = new_server()
    await server.init()
    idx = await server.register_namespace("http://machine-agent/plant")
    settings = Settings()
    return server, await build_address_space(
        server, idx, settings.buffer_capacity, settings.joining_distance_nominal
    )


async def _children(node: Node) -> set[str]:
    return {
        (await child.read_browse_name()).Name for child in await node.get_children()
    }


@pytest_asyncio.fixture
async def space() -> AddressSpace:
    _server, built = await _build()
    return built


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
async def test_every_station_can_raise_an_alarm(space: AddressSpace) -> None:
    """§4.2's alarm type, on all four stations rather than on the one with a condition.

    §5.2 keys an alarm on `station_id` and §3.7's screen lists them per station, so a
    type declared on S2 alone would make that column a constant the schema pretends is a
    variable -- and the station that gains the second condition would be a change to the
    address space rather than to one file.

    Asserted on the generators the tree actually built, not on `events.ALARM.stations`:
    a station named in that tuple and given no generator raises `ValueError` on its
    first alarm, in a plant that has already written history.
    """
    for code, nodes in space.stations.items():
        assert ALARM.name in nodes.generators, code
        assert ALARM in event_types_for(code), code
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
async def test_d12_nodes_are_in_the_tree_and_out_of_the_history(
    space: AddressSpace,
) -> None:
    """D12: `Lane1_Lot`, `Lane2_Lot` and `CurrentAssemblySerial` are readable live and
    never historised, because `ComponentReadEvent` and `AssemblyCreatedEvent` carry the
    same facts authoritatively and a historised second copy invites the time-join
    §3.4a forbids.

    Both halves, because each alone is satisfiable by the wrong tree: absent from
    `historised` is also true of a node nobody built, and present in `live` is also
    true of a node that is historised as well.
    """
    s1 = space.stations["S1_Feeding"]
    assert set(s1.live) == _LIVE_VARIABLES["S1_Feeding"]
    assert set(s1.live) & set(s1.historised) == set()
    for owner, signal, *_ in historised_streams(space):
        assert (owner, signal) not in {
            ("S1_Feeding", name) for name in _LIVE_VARIABLES["S1_Feeding"]
        }


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
    """§3.1: the stations do NOT share one takt, and `Station._nominal_takt` looks the
    number up by `StationNodes.code` -- these browse names. `Station.__init__` now
    refuses a station the settings do not name, so a drift between the two spellings
    is loud rather than a silently balanced line; this pins the *shipped defaults*
    against §4.1's codes, so the plant boots without needing the environment to
    correct it. `test_stations` covers the refusal itself."""
    assert set(space.stations) <= set(Settings().station_takt_seconds)


@pytest.mark.asyncio
async def test_topology_is_browsable_under_line_stations() -> None:
    """§4.1: the gateway discovers the line's topology by browsing, never by config.

    Walked from Objects by browse name, not from `space.stations_folder`: an
    in-process handle proves the object exists, not that the path a gateway actually
    walks reaches it.
    """
    server, space = await _build()
    line = await server.nodes.objects.get_child([f"{space.idx}:Line"])
    stations = await line.get_child([f"{space.idx}:Stations"])

    found = [(await s.read_browse_name()).Name for s in await stations.get_children()]
    assert found == ["S1_Feeding", "S2_Joining", "S3_Inspection", "S4_Outfeed"]


@pytest.mark.asyncio
async def test_the_served_tree_browses_to_twenty_five_streams_ten_static_three_live() -> (
    None
):
    """§4.1's count, read off the server a gateway meets rather than off the dataclass
    that built it.

    `test_the_tree_carries_exactly_twenty_five_historised_streams` sums
    `len(nodes.historised)`, and every assertion keyed on `historised` shares that
    blind spot: a variable simply left out of `historised` keeps all of them green
    while a gateway browsing S1 sees it anyway. This walks the tree instead, so what is
    asserted is what is served.

    38 variables, and the split is the point: 25 historised streams (every station
    signal, plus one `Level` per buffer), ten static nodes read on connect -- the
    buffers' three apiece and `Press/StrokeLength`, §3.4a's curve axis -- and D12's
    three live-only ones on S1.

    **Those three are station children, and `TopologyDiscovery.DiscoverStationsAsync`
    takes every Variable child of a station as a signal to subscribe to without ever
    reading the `Historizing` attribute.** So the gateway will discover 28 streams
    against a plant that historises 25 and a ledger with 25 rows in it, and reconcile
    three streams that can have no rows at all. Task 6 is where that is fixed, and this
    count is where it shows. `Press/StrokeLength` sits outside Stations precisely to
    avoid being a 26th; §4.1 puts these three on S1, so they cannot.
    """
    server, space = await _build()
    line = await server.nodes.objects.get_child([f"{space.idx}:Line"])

    stations = await line.get_child([f"{space.idx}:Stations"])
    served_stations = {
        (await station.read_browse_name()).Name: await _children(station)
        for station in await stations.get_children()
    }
    assert served_stations == {
        code: names | _LIVE_VARIABLES[code]
        for code, names in _HISTORISED_VARIABLES.items()
    }

    buffers = await line.get_child([f"{space.idx}:Buffers"])
    served_buffers = {
        (await buffer.read_browse_name()).Name: await _children(buffer)
        for buffer in await buffers.get_children()
    }
    assert served_buffers == dict.fromkeys(("B1_2", "B2_3", "B3_4"), _BUFFER_VARIABLES)

    press = await line.get_child([f"{space.idx}:Press"])
    assert await _children(press) == {"StrokeLength"}

    # Only Level is historised on a buffer; Capacity, UpstreamStation and
    # DownstreamStation are nine of the ten statics, and the press's stroke is the
    # tenth.
    live = sum(len(names) for names in _LIVE_VARIABLES.values())
    historised = sum(len(names) for names in served_stations.values()) - live
    historised += len(served_buffers)
    static = sum(len(names) - 1 for names in served_buffers.values())
    static += len(await _children(press))
    assert (historised, static, live) == (25, 10, 3)
    assert historised + static + live == 38


async def _properties(event_type: Node) -> list[str]:
    return [
        (await p.read_browse_name()).Name for p in await event_type.get_properties()
    ]


@pytest.mark.asyncio
async def test_custom_event_fields_decode_first_and_in_declared_order(
    space: AddressSpace,
) -> None:
    """The wire format, for all five event types. An event notification is a positional
    `EventFieldList` matching the SelectClauses asked for, so a reordered field table
    silently re-assigns every column Task 6 decodes by position.

    `get_properties()` also returns BaseEventType's own inherited fields (13 of them);
    this pins only that ours lead, in the declared order. Asserted against literals for
    the reason `_EVENT_FIELD_ORDER` records.
    """
    for event, expected in _EVENT_FIELD_ORDER.items():
        names = await _properties(space.event_types[event.name])
        assert names[: len(expected)] == expected, event.name


@pytest.mark.asyncio
async def test_the_four_new_event_types_carry_no_image(space: AddressSpace) -> None:
    """§3.4: only rejects carry an image, and only from S3. Task 4's per-event-type page
    sizing (D4) rests on this being true rather than merely intended -- paging an
    imageless stream at S3's 25 costs ~3,200 round trips where 80 would do."""
    for event in _EVENT_FIELD_ORDER:
        carries = "Image" in await _properties(space.event_types[event.name])
        assert carries == (event is INSPECTION_RESULT), event.name


@pytest.mark.asyncio
async def test_a_station_refuses_an_event_type_it_does_not_emit(
    space: AddressSpace,
) -> None:
    """§4.1 gives each event type exactly one emitting station. A station handed
    another's must fail loudly rather than drop it: every station now has a generator,
    so a mistyped event type would otherwise reach a real `trigger` on the wrong node
    and be attributed to the wrong station for the rest of the run."""
    with pytest.raises(ValueError, match="does not emit"):
        await space.stations["S1_Feeding"].trigger_event(
            INSPECTION_RESULT, datetime(2026, 9, 13, tzinfo=UTC), {}
        )


@pytest.mark.asyncio
async def test_an_event_missing_a_field_is_refused(space: AddressSpace) -> None:
    """EventGenerator reuses one Event object across every trigger, so a field left
    out keeps the previous event's value and goes out as if it were this one's -- a
    defect class attributed to the wrong serial, with nothing raised."""
    with pytest.raises(ValueError, match="InspectionResultEventType"):
        await space.stations["S3_Inspection"].trigger_event(
            INSPECTION_RESULT,
            datetime(2026, 9, 13, tzinfo=UTC),
            {"AssemblySerial": "A-00000001"},
        )


@pytest.mark.asyncio
async def test_clock_is_a_sibling_of_stations_with_exactly_three_variables() -> None:
    """§4.1: Clock sits under Line, next to Stations and Buffers -- not folded into
    either, since it describes the line's own timebase rather than something a
    station measures -- carrying exactly SimulatedTime, Phase and Speed, browsable by
    the same path a gateway walks to find Stations."""
    server, space = await _build()
    line = await server.nodes.objects.get_child([f"{space.idx}:Line"])

    assert await _children(line) == {"Clock", "Stations", "Buffers", "Press"}

    press = await line.get_child([f"{space.idx}:Press"])
    assert await _children(press) == {"StrokeLength"}

    clock = await line.get_child([f"{space.idx}:Clock"])
    assert await _children(clock) == {"SimulatedTime", "Phase", "Speed"}


@pytest.mark.asyncio
async def test_clock_nodes_track_simulated_clock_through_both_phases() -> None:
    """Phase and Speed must agree with SimulatedClock.phase/catchup_speed exactly --
    not a second, independent notion of either computed here (see
    address_space.publish_clock and its _clock_snapshot helper). Speed is exactly
    1.0 once live, never the configured catchup_speed (§3.2).

    Runs against a *started* server, not the bare address space: publish_clock's only
    caller (server.main) runs it inside `async with server`, and a node write behaves
    differently there -- the subscription machinery that a historian or a client hangs
    off is live. This is the one test that covers it.
    """
    server, space = await _build()
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
    server, space = await _build()
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
    """Reconciliation covers the historised streams and nothing else (see
    build_address_space's comment on the Clock object). Historising the clock -- or
    D12's three live-only nodes -- would change those counts, which is exactly the
    defect this guards against: the historian's own handler set, not a count that could
    stay right by accident.

    Asserted as a size and an absence rather than against `historised_streams`, which
    is the enumeration the historian attaches from -- comparing the two would compare
    the code to itself.
    """
    server, space = await _build()

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
    # §4.1's 25 streams plus the four emitting station nodes: event history is
    # attached to the emitting object rather than to a variable, and one node's
    # handler covers every event type it emits.
    assert len(historized) == 29
    assert {space.stations[code].node for code in _HISTORISED_VARIABLES} <= historized
    s1 = space.stations["S1_Feeding"]
    assert set(s1.live.values()) & historized == set()
    assert {
        space.clock_time,
        space.clock_phase,
        space.clock_speed,
    } & historized == set()


@pytest.mark.asyncio
async def test_every_event_type_historises_with_all_its_columns(
    tmp_path: Path,
) -> None:
    """M1's ORDER MATTERS defect, five times over.

    `get_event_generator` adds the GeneratesEvent reference that `historize_node_event`
    later reads to decide which event types get columns; a generator created after it
    yields an event table with no columns for that type and no error anywhere --
    asyncua's own `historize_event` docstring says adding an event type afterwards is
    unsupported and that the table has to be deleted by hand.

    Read out of sqlite rather than off the generators, because the generators existing
    is exactly what the defect leaves intact: the table's columns are the only place
    the ordering shows. Column names are the event fields' *display* names, so this
    also pins that S1's two event types share one table without colliding -- two
    same-named fields on one emitting node would collapse to one column, or fail the
    CREATE TABLE inside HistorySQLite's own `except aiosqlite.Error`.
    """
    server, space = await _build()
    storage = await attach_historian(
        server,
        space,
        tmp_path / "h.db",
        1000,
        datetime(2026, 9, 12, tzinfo=UTC),
        Ledger(),
    )

    for event, expected in _EVENT_FIELD_ORDER.items():
        for code in event.stations:
            table = _table_name(storage, space.stations[code].node.nodeid)
            async with storage._db.execute(f'PRAGMA table_info("{table}")') as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            assert set(expected) <= columns, f"{event.name} on {code}"
