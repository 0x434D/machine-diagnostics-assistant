"""Objects/Line/{Clock, Stations, Buffers} -- §4.1's tree, in full.

Four stations and three buffers, built from the tables below rather than from seven
near-identical code paths. Adding a fifth station is a row of data and nothing else,
which is the same claim §4.1 makes about the gateway: topology is described in one
place and discovered by browsing, never spread across code.

**What the tree counts to: 38 variables -- 25 historised streams, ten static nodes and
three live-only ones.** Six historised each for S1, S2 and S4, four for S3, one `Level`
per buffer; nine of the statics are the buffers' `Capacity`, `UpstreamStation` and
`DownstreamStation` and the tenth is `Press/StrokeLength`, all read on connect and
never historised. Every guard in Tasks 9 and 10 is sized against the 25, so a tree that
counts to 24 or 26 is a tree that disagrees with §4.1.

The three live-only ones are D12's: `Lane1_Lot`, `Lane2_Lot` and
`CurrentAssemblySerial`, in the tree and readable by the HMI, never historised. The
events carry the same facts authoritatively (§3.4a), and a historised second copy is
a weaker one that invites exactly the time-join the spec forbids. **They are station
children, and a gateway that subscribes to every Variable child of a station without
reading its `Historizing` attribute will ingest 28 streams against a ledger with 25
rows in it.** `Press/StrokeLength` sits outside Stations for precisely that reason;
these three cannot, because §4.1 puts them on S1.

`Press/StrokeLength` is §3.4a's curve axis: the curve is an array of forces with no
axis of its own, and this is the ram travel that turns a sample index into
millimetres. It is a sibling of Stations rather than a variable on S2 **because a
station's variables are what a gateway subscribes to** -- `TopologyDiscovery` takes
every Variable child of a station as a signal, so a static one there would arrive as a
26th stream. Published rather than left as a constant in the gateway and another in the
analysis service: only OPC UA crosses between the stacks, and three private copies of
one number are three chances to mis-scale every curve downstream with no error
anywhere.

**A deviation from §4.1's wording, recorded rather than absorbed.** §4.1 says the
buffer nodes *reference* the stations they sit between. `UpstreamStation` and
`DownstreamStation` below are `String` variables carrying the station's browse name,
not a custom OPC UA reference type. The topology is equally discovered either way --
the gateway browses and reads rather than being configured -- and a custom
hierarchical reference type adds asyncua/UA-.NETStandard interop risk for no
diagnostic gain. Raised with and accepted by the spec owner; §4.1's wording should
say "carry" rather than "reference".

**The declared initial values are historised, and that is a decision.** OPC UA
mandates an initial-value notification when a monitored item is created, and asyncua
delivers it unconditionally -- `_is_data_changed` returns True whenever the previous
value is None, and `create_monitored_item` fires the callback the moment the item
exists. On a historised variable that is one row per stream, 25 of them, before any
station has written anything, and nothing short of not historising the stream avoids
it. The choice is therefore only between a truthful priming row and a false one, so
every value declared below is one the plant is genuinely in before its driver runs:
`Aborted` with no reason for PackML, zero for every counter, level and float.
`historian.attach_historian` owns the other half: it rewrites each of the 25 with a
deliberate simulated `SourceTimestamp` *before* historising them, or the row lands
stamped with the real wall clock, and it counts all 25 into the ledger because no
generator produced them.

**A write is not a row.** asyncua's monitored-item filter drops a notification whose
value equals the previous one, so a stream is historised only where it actually
changes. S4 writes `GoodCount` and `RejectCount` on every part but only one of them
moves; `State` and `StateReason` repeat for as long as a station stays put; a buffer
`Level` written every cycle repeats whenever the level does. A ledger that counts
writes over-counts every one of those. `TaktTime` is the one exemption in practice --
not by construction: D13 deleted `Station.next_takt`'s resample-until-distinct guard,
and what keeps the stream distinct now is that a continuous Gaussian draw collides with
its predecessor essentially never (0 in 600,000 draws, measured). It is a *stream that
happens not to repeat*, not a stream that cannot, and `config.takt_jitter_sigma`'s
validator is what stops a configuration flattening it. `historian.Ledger` applies
asyncua's rule regardless, which is what makes its count comparable to the historian's
whether or not a value repeats.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NamedTuple

from asyncua import Node, Server, ua
from asyncua.server import EventGenerator

from simulator.clock import Phase, SimulatedClock
from simulator.events import EVENT_TYPES, EventType, event_types_for
from simulator.packml import State

# §4.1's per-station variables, as data. The signals here are what each station
# measures; the two every station carries are in PACKML_SIGNALS below.
STATION_SIGNALS: dict[str, tuple[tuple[str, ua.VariantType], ...]] = {
    "S1_Feeding": (
        ("TaktTime", ua.VariantType.Double),
        ("LaneFill_1", ua.VariantType.Double),
        ("LaneFill_2", ua.VariantType.Double),
        ("PartCount", ua.VariantType.UInt32),
    ),
    "S2_Joining": (
        ("TaktTime", ua.VariantType.Double),
        ("JoiningForcePeak", ua.VariantType.Double),
        ("JoiningDistance", ua.VariantType.Double),
        ("PartCount", ua.VariantType.UInt32),
    ),
    "S3_Inspection": (
        ("TaktTime", ua.VariantType.Double),
        ("PartCount", ua.VariantType.UInt32),
    ),
    "S4_Outfeed": (
        ("TaktTime", ua.VariantType.Double),
        ("OutfeedFill", ua.VariantType.Double),
        ("GoodCount", ua.VariantType.UInt32),
        ("RejectCount", ua.VariantType.UInt32),
    ),
}
"""Insertion order is line order, and `build_address_space` browses out in this order.
A gateway discovers direction from the buffers' Upstream/DownstreamStation, not from
this, but a Stations folder listing S4 before S1 would be needlessly confusing to
anyone holding UaExpert."""

STATION_LIVE_SIGNALS: dict[str, tuple[tuple[str, ua.VariantType], ...]] = {
    "S1_Feeding": (
        ("Lane1_Lot", ua.VariantType.String),
        ("Lane2_Lot", ua.VariantType.String),
        ("CurrentAssemblySerial", ua.VariantType.String),
    ),
}
"""D12's live-only variables: in the tree, written every cycle, never historised.

Keyed by station like STATION_SIGNALS and deliberately not merged into it -- the two
tables are read by different things (`historised_streams` walks one of them and the
ledger counts what it writes), and a signal that moved between them by accident would
either lose its history or gain a second copy of a fact an event already carries.

§4.1 puts all three on S1, which is where both facts are made: the lot a lane is
drawing from and the serial of the assembly just created. A station with no entry here
has no live-only variables, which is every station but S1.
"""

# Every station carries both (§4.1). Separate from the table above because they are
# the same for all four, and because their VariantType is what makes them
# un-deadbandable on the gateway side (Task 9's signal policy defaults).
PACKML_SIGNALS: tuple[tuple[str, ua.VariantType], ...] = (
    ("State", ua.VariantType.String),
    ("StateReason", ua.VariantType.String),
)

# (buffer_id, upstream station, downstream station). The station names are browse
# names, and must stay equal to STATION_SIGNALS' keys -- that equality is the whole
# of the discovered topology.
BUFFERS: tuple[tuple[str, str, str], ...] = (
    ("B1_2", "S1_Feeding", "S2_Joining"),
    ("B2_3", "S2_Joining", "S3_Inspection"),
    ("B3_4", "S3_Inspection", "S4_Outfeed"),
)

PRESS_FOLDER = "Press"
CURVE_STROKE_SIGNAL = "StrokeLength"
"""Where §3.4a's curve axis is published, and under what name."""

INSPECTION_STATION = "S3_Inspection"
"""The station §4.1 gives the inspection event to, named because the historian and
`AddressSpace.inspection` reach for it directly. The other four event types belong to
other stations; `events.EVENT_TYPES` is what says which."""

BUFFER_LEVEL_SIGNAL = "Level"
"""The one historised variable a buffer has. Named because the ledger and the
reconciliation key streams by it and a buffer's own node set does not spell it."""

BUFFER_LEVEL_TYPE = ua.VariantType.UInt32
"""A buffer holds a whole number of carriers and can never hold fewer than none.

Named because a buffer's `Level` is declared in one place and written in another,
and the station signals' types travel with them in `StationNodeSet.types` -- this is
the one variant type that would otherwise be a literal in two spots, free to drift
into a node declared UInt32 and written as something else."""

_INITIAL_BY_TYPE: dict[ua.VariantType, float | str] = {
    ua.VariantType.Double: 0.0,
    ua.VariantType.UInt32: 0,
    ua.VariantType.String: "",
}

_INITIAL_OVERRIDES: dict[str, float | str] = {"State": State.ABORTED.value}
"""`State` is the one signal whose true starting value is not its type's zero. A
station that has never been cleared is `Aborted` -- where `packml.StateMachine`
starts, and what `line.Line.__init__` constructs four of -- with no reason. An empty
string is a state no PackML machine is ever in, and this value gets historised (see
the module docstring), so it would be a fake with a row of its own."""


# Written once at build time, before publish_clock's first tick has run (that tick
# starts in the same TaskGroup as simulator.status.publish -- see server.main, and
# follows within one status_interval_seconds of boot). The epoch is a self-evidently
# bootstrap instant, not a real one, and not a call to datetime.now(): nothing outside
# clock.py's own injected wall_fn may read the real clock (see clock.SimulatedClock.wall).
_BOOTSTRAP_TIME = datetime(1970, 1, 1, tzinfo=UTC)


async def write_at(
    node: Node, variant_type: ua.VariantType, at: datetime, value: float | str
) -> None:
    """Write `value` to `node` with `at` as its SourceTimestamp.

    The only place in the simulator that sets a historised node's value -- stations
    write through `StationNodeSet.write`, buffers through `BufferNodeSet.write_level`
    and the historian's priming through this directly -- so the suppression below is
    carried once rather than at four call sites saying the same measured thing.
    """
    # SourceTimestamp= carries a suppression for the reason M1 measured and wrote
    # down: DataValue types the field as ua.DateTime, a datetime subclass asyncua's
    # own runtime never constructs. sqlite3's parameter binder matches by exact type
    # since 3.12 deprecated the implicit subclass adapter, so a real ua.DateTime
    # raises "type 'DateTime' is not supported" *inside* HistorySQLite's own
    # save_node_value try/except -- silently logged, not raised, and every row goes
    # missing with no visible error. A plain datetime is what the storage layer needs.
    await node.write_value(
        ua.DataValue(
            ua.Variant(value, variant_type),
            SourceTimestamp=at,  # type: ignore[arg-type]
        )
    )


@dataclass(frozen=True)
class StationNodeSet:
    """One station's handles. Implements `stations.StationNodes`.

    `historised` is every signal §4.1 asks this station to record; `live` is D12's
    un-historised ones. The split is the whole of what "historised" means here, and
    `historised_streams` walks only the first -- so a signal in the wrong dict either
    loses its history or gains a second copy of a fact an event already carries.

    `generators` is keyed by event *type* browse name, because a station may emit more
    than one (S1 emits two) and the emitting node is the same for both.
    """

    code: str
    node: Node
    historised: dict[str, Node]
    live: dict[str, Node]
    types: dict[str, ua.VariantType]
    generators: dict[str, EventGenerator]

    async def write(self, signal: str, at: datetime, value: float | str) -> None:
        """Publish one of this station's historised signals at simulated instant `at`.

        Raises KeyError if `signal` is not one §4.1 gives this station.
        """
        # Bound to the variant type the node was *declared* with, never to the runtime
        # type of `value`: PartCount, GoodCount and RejectCount arrive as `int`
        # through a parameter PEP 484 types `float`, so anything dispatching on
        # isinstance(value, float) would miss every counter in the line.
        await write_at(self.historised[signal], self.types[signal], at, value)

    async def write_live(self, signal: str, at: datetime, value: float | str) -> None:
        """Publish one of D12's live-only variables. Raises KeyError for a signal this
        station does not have one of.

        Separate from `write` rather than dispatching inside it, so that a station
        cannot reach a historised node through the un-counted path or the reverse: the
        ledger counts every `write` and must count no `write_live`, and a single method
        deciding by dictionary membership would silently do the wrong one after a typo.

        Still stamped with simulated time. Nothing reads these from history -- there is
        none -- but a live reader that saw the wall clock here would be reading a
        different clock from the one every other node on this tree carries.
        """
        await write_at(self.live[signal], self.types[signal], at, value)

    async def trigger_event(
        self, event: EventType, at: datetime, fields: dict[str, object]
    ) -> None:
        """Fire one of this station's event types with `at` as its Time.

        `fields` must name exactly `event.fields`. Raises ValueError if it does not, or
        if `event` is not one this station emits.
        """
        generator = self.generators.get(event.name)
        if generator is None:
            raise ValueError(
                f"{self.code} does not emit {event.name}: §4.1 gives it "
                f"{sorted(self.generators) or 'no event type'}"
            )
        declared = set(event.field_names)
        if set(fields) != declared:
            # Not pedantry, and not merely a typo guard. EventGenerator reuses one
            # Event object across every trigger, so a field left out keeps the
            # *previous* event's value and is sent as if it were this one's -- a
            # quiet wrong answer with a defect class attached to the wrong serial.
            raise ValueError(
                f"{self.code} event fields {sorted(fields)} are not {event.name}'s "
                f"{sorted(declared)}; a missing field is sent as the previous "
                "event's value, not as absent"
            )
        for name, value in fields.items():
            setattr(generator.event, name, value)
        # Message is a display string nothing in this system reads back; the fields
        # carry the payload.
        await generator.trigger(time_attr=at, message=self.code)


@dataclass(frozen=True)
class BufferNodeSet:
    """One buffer's handles.

    `capacity`, `upstream` and `downstream` are written once in
    `build_address_space` and never again -- they are §4.1's static topology, read on
    connect. Only `level` is historised.
    """

    buffer_id: str
    node: Node
    level: Node
    capacity: Node
    upstream: Node
    downstream: Node

    async def write_level(self, at: datetime, level: int) -> None:
        """Publish this buffer's fill at simulated instant `at`."""
        await write_at(self.level, BUFFER_LEVEL_TYPE, at, level)


@dataclass(frozen=True)
class AddressSpace:
    idx: int
    line: Node
    clock: Node
    press_stroke: Node
    clock_time: Node
    clock_phase: Node
    clock_speed: Node
    stations_folder: Node
    buffers_folder: Node
    stations: dict[str, StationNodeSet]
    buffers: dict[str, BufferNodeSet]
    event_types: dict[str, Node]

    @property
    def inspection(self) -> StationNodeSet:
        """S3, which several tests and the image accounting reach for by name.

        A property rather than a field so there is one S3 in this object, not a second
        alias of it that could be built wrong. M1's `s3`/`takt`/`part_count`/`event_gen`
        handles were the same idea for a tree with one station in it; they went with
        `station_s3.py`, because a handle naming S3's TaktTime as *the* TaktTime is
        exactly the confusion a four-station line cannot afford.
        """
        return self.stations[INSPECTION_STATION]

    def emitting_stations(self) -> Iterator[StationNodeSet]:
        """Every station with at least one event generator, in line order.

        What `attach_historian` historises event history against: event history hangs
        off the emitting node rather than off a variable, and one node's table holds
        every event type it emits.
        """
        return (nodes for nodes in self.stations.values() if nodes.generators)


class HistorisedStream(NamedTuple):
    """One of §4.1's 25 historised streams, keyed the way the ledger keys it.

    `owner` is a station browse name or a buffer id -- the two kinds of thing §4.1
    gives a historised variable to. It is half the key because `S1_Feeding.TaktTime`
    and `S2_Joining.TaktTime` are two streams, and a count that merged them would
    reconcile perfectly while one of them lost every row.
    """

    owner: str
    signal: str
    node: Node
    variant_type: ua.VariantType


def historised_streams(space: AddressSpace) -> Iterator[HistorisedStream]:
    """Every variable the historian records, stations in line order then buffers.

    One enumeration behind three lists that must name the same 25 nodes -- what is
    primed, what is historised, and what is counted back out of the historian -- so
    they cannot drift apart. A stream primed but not historised has a row in the
    ledger and none in the database; historised but not primed has the reverse, with
    a wall-clock timestamp on it.
    """
    for nodes in space.stations.values():
        for signal, node in nodes.historised.items():
            yield HistorisedStream(nodes.code, signal, node, nodes.types[signal])
    for buffer_id, buffer_nodes in space.buffers.items():
        yield HistorisedStream(
            buffer_id, BUFFER_LEVEL_SIGNAL, buffer_nodes.level, BUFFER_LEVEL_TYPE
        )


def initial_value(signal: str, variant_type: ua.VariantType) -> float | str:
    """What a variable declares before anything writes to it -- and historises once.

    Raises KeyError for a variant type with no declared zero, which is the right
    failure for a signal added to the tables above without deciding what it starts at.
    """
    return _INITIAL_OVERRIDES.get(signal, _INITIAL_BY_TYPE[variant_type])


async def build_address_space(
    server: Server, idx: int, buffer_capacity: int, curve_stroke_mm: float
) -> AddressSpace:
    """§4.1's tree under Objects/Line, on an initialised but unstarted `server`.

    `buffer_capacity` is `Settings.buffer_capacity` -- the same number the `Buffer`
    objects themselves are built with, so the `Capacity` node a gateway reads and the
    capacity the line actually enforces are one configured value, not two.
    `curve_stroke_mm` is the same idea for §3.4a's curve: the ram travel the samples are
    spaced across, so the axis a consumer scales with and the axis the press was sampled
    on cannot be two different numbers.
    """
    objects = server.nodes.objects
    line = await objects.add_object(idx, "Line")

    # Clock is a sibling of Stations (§4.1), not a station itself: it exposes the
    # line's own timebase, not something a station measures. Deliberately never
    # historised (see publish_clock) -- reconciliation covers §4.1's 25 streams plus
    # the inspection event, and these three nodes have no ledger entry to reconcile:
    # they are operational metadata the gateway reads live to tell catch-up from live
    # (§4.3), not process data anyone replays from history.
    clock = await line.add_object(idx, "Clock")
    clock_time = await clock.add_variable(
        idx, "SimulatedTime", _BOOTSTRAP_TIME, ua.VariantType.DateTime
    )
    clock_phase = await clock.add_variable(
        idx, "Phase", Phase.CATCHUP.value, ua.VariantType.String
    )
    clock_speed = await clock.add_variable(idx, "Speed", 1.0, ua.VariantType.Double)

    # Static, written by add_variable's own initial value and never again -- the same
    # arrangement as the buffers' topology nodes below, and readable on connect without
    # the plant running. See this module's docstring for why it is not a child of S2.
    press = await line.add_object(idx, PRESS_FOLDER)
    press_stroke = await press.add_variable(
        idx, CURVE_STROKE_SIGNAL, curve_stroke_mm, ua.VariantType.Double
    )

    # create_custom_event_type wants a list, not the tuple each field table is defined
    # as elsewhere -- list() here, not a per-element rebuild.
    event_types = {
        event.name: await server.create_custom_event_type(
            idx, event.name, ua.ObjectIds.BaseEventType, list(event.fields)
        )
        for event in EVENT_TYPES
    }

    stations_folder = await line.add_object(idx, "Stations")
    stations: dict[str, StationNodeSet] = {}
    for code, signals in STATION_SIGNALS.items():
        station = await stations_folder.add_object(idx, code)
        historised: dict[str, Node] = {}
        types: dict[str, ua.VariantType] = {}
        for name, variant_type in PACKML_SIGNALS + signals:
            historised[name] = await station.add_variable(
                idx, name, initial_value(name, variant_type), variant_type
            )
            types[name] = variant_type

        # D12: in the tree, never historised, and so never primed and never counted.
        # Declared with the same initial_value as everything else so a live reader
        # before the first cycle sees an empty string rather than whatever asyncua
        # would have defaulted to.
        live: dict[str, Node] = {}
        for name, variant_type in STATION_LIVE_SIGNALS.get(code, ()):
            live[name] = await station.add_variable(
                idx, name, initial_value(name, variant_type), variant_type
            )
            types[name] = variant_type

        generators: dict[str, EventGenerator] = {}
        for event in event_types_for(code):
            # ORDER MATTERS, once per generator and now five times. get_event_generator
            # adds the GeneratesEvent reference from the emitting node to the event type
            # and sets its EventNotifier bit. historize_node_event() later reads exactly
            # those GeneratesEvent references to decide which event types to historise
            # -- so every generator a station will ever have must exist before it, or
            # event history is silently created with columns for the types that did
            # exist and none for the rest. asyncua says as much in historize_event's own
            # docstring: adding custom events to a source AFTER historising is not
            # supported, and the table has to be deleted by hand to recover.
            #
            # station.nodeid, not station: get_event_generator's emitting_node is
            # typed NodeId | int. Its implementation does accept a Node
            # (event_generator.py isinstance-checks for one), but that path is missing
            # from the public signature -- passing the NodeId reaches the identical
            # Node internally and satisfies mypy strict either way.
            generators[event.name] = await server.get_event_generator(
                event_types[event.name], station.nodeid
            )

        stations[code] = StationNodeSet(
            code=code,
            node=station,
            historised=historised,
            live=live,
            types=types,
            generators=generators,
        )

    buffers_folder = await line.add_object(idx, "Buffers")
    buffers: dict[str, BufferNodeSet] = {}
    for buffer_id, upstream, downstream in BUFFERS:
        buffer = await buffers_folder.add_object(idx, buffer_id)
        # Keyword arguments throughout: these are six same-typed Node slots filled by
        # inline awaits, where transposing two add_variable calls binds Capacity to
        # UpstreamStation and type-checks perfectly.
        buffers[buffer_id] = BufferNodeSet(
            buffer_id=buffer_id,
            node=buffer,
            # Through initial_value like every station signal, rather than a literal
            # 0: the historian primes this node from that same function, and a
            # declaration that disagreed with the priming value would historise two
            # rows where §4.1 has one stream starting at one value.
            level=await buffer.add_variable(
                idx,
                BUFFER_LEVEL_SIGNAL,
                initial_value(BUFFER_LEVEL_SIGNAL, BUFFER_LEVEL_TYPE),
                BUFFER_LEVEL_TYPE,
            ),
            # The static three. Written here by add_variable's own initial value and
            # never again, which is what makes them readable on connect without the
            # plant running -- §4.1's "topology is discovered, not configured".
            capacity=await buffer.add_variable(
                idx, "Capacity", buffer_capacity, ua.VariantType.UInt32
            ),
            upstream=await buffer.add_variable(
                idx, "UpstreamStation", upstream, ua.VariantType.String
            ),
            downstream=await buffer.add_variable(
                idx, "DownstreamStation", downstream, ua.VariantType.String
            ),
        )

    return AddressSpace(
        idx=idx,
        line=line,
        clock=clock,
        press_stroke=press_stroke,
        clock_time=clock_time,
        clock_phase=clock_phase,
        clock_speed=clock_speed,
        stations_folder=stations_folder,
        buffers_folder=buffers_folder,
        stations=stations,
        buffers=buffers,
        event_types=event_types,
    )


def _clock_snapshot(clock: SimulatedClock) -> tuple[datetime, Phase, float]:
    """The (SimulatedTime, Phase, Speed) triple Clock's three nodes carry, read from
    `SimulatedClock`'s public API only -- nothing outside clock.py may touch `_cfg`.

    Speed is the actual speed for the current phase, not the configured
    catchup_speed once live: §3.2 fixes live speed at exactly 1.0, and a test
    elsewhere asserts that equality, not an approximation. Phase is `clock.phase`
    itself, not re-derived, so there is exactly one place that decides which phase
    the plant is in -- not a second one here that could disagree with it.
    """
    phase = clock.phase
    speed = 1.0 if phase is Phase.LIVE else clock.catchup_speed
    return clock.now(), phase, speed


async def publish_clock(
    space: AddressSpace, clock: SimulatedClock, interval_seconds: float
) -> None:
    """Keep Clock.SimulatedTime/Phase/Speed current, forever, every `interval_seconds`.

    Started alongside simulator.status.publish in the same TaskGroup (see
    server.main) rather than on a second timer -- one cadence for both, not two
    that could drift apart. Not historised: see build_address_space's comment on
    the Clock object for why the historised-stream count depends on that.
    """
    while True:
        now, phase, speed = _clock_snapshot(clock)
        await space.clock_time.write_value(now, ua.VariantType.DateTime)
        await space.clock_phase.write_value(phase.value, ua.VariantType.String)
        await space.clock_speed.write_value(speed, ua.VariantType.Double)
        await asyncio.sleep(interval_seconds)
