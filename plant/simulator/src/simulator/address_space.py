"""Objects/Line/{Clock, Stations, Buffers} -- §4.1's tree, in full.

Four stations and three buffers, built from the tables below rather than from seven
near-identical code paths. Adding a fifth station is a row of data and nothing else,
which is the same claim §4.1 makes about the gateway: topology is described in one
place and discovered by browsing, never spread across code.

**What the tree counts to: 25 historised streams and nine static nodes.** Six each
for S1, S2 and S4, four for S3, one `Level` per buffer; the static nine are the
buffers' `Capacity`, `UpstreamStation` and `DownstreamStation`, read on connect and
never historised. Every guard in Tasks 9 and 10 is sized against the 25, so a tree
that counts to 24 or 26 is a tree that disagrees with §4.1.

`Lane1_Lot`, `Lane2_Lot` and `CurrentAssemblySerial` are in §4.1 and are deliberately
absent here. They arrive in M2b with the data that makes them change; §13's standard
is that nothing in a milestone is faked, and a node holding a constant is a fake.

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
Task 7 owns the other half -- it must write each of the 25 once with a deliberate
simulated `SourceTimestamp` *before* attaching the historian (M1's `attach_historian`
does exactly this for its two), or the row lands stamped with the real wall clock,
and its ledger must count 25 rows no generator produced.

**A write is not a row.** asyncua's monitored-item filter drops a notification whose
value equals the previous one, so a stream is historised only where it actually
changes. S4 writes `GoodCount` and `RejectCount` on every part but only one of them
moves; `State` and `StateReason` repeat for as long as a station stays put; a buffer
`Level` written every cycle repeats whenever the level does. A ledger that counts
writes over-counts every one of those. `TaktTime` is the one exemption, and only
because `Station.next_takt` resamples until the value differs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from asyncua import Node, Server, ua
from asyncua.server import EventGenerator

from simulator.clock import Phase, SimulatedClock
from simulator.events import EVENT_FIELDS
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

INSPECTION_STATION = "S3_Inspection"
"""The one station §4.1 gives an event type in M2a. The other four event types in
§4.1's tree arrive with the data they carry, in M2b."""

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


async def _write_at(
    node: Node, variant_type: ua.VariantType, at: datetime, value: float | str
) -> None:
    """Write `value` to `node` with `at` as its SourceTimestamp.

    The only place in this module that sets a node's value after build time, so the
    suppression below is carried once rather than at every call site.
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

    `historised` is every signal §4.1 asks this station to publish; there is no
    un-historised station variable in M2a, which is why the name is safe to count
    against. Historisation itself is attached elsewhere (`historian`), against
    exactly these nodes.
    """

    code: str
    node: Node
    historised: dict[str, Node]
    types: dict[str, ua.VariantType]
    event_gen: EventGenerator | None

    def require_event_generator(self) -> EventGenerator:
        """Raises ValueError for a station that emits no events."""
        if self.event_gen is None:
            raise ValueError(
                f"{self.code} emits no events: in M2a §4.1's event types exist for "
                f"{INSPECTION_STATION} only"
            )
        return self.event_gen

    async def write(self, signal: str, at: datetime, value: float | str) -> None:
        """Publish one of this station's signals at simulated instant `at`.

        Raises KeyError if `signal` is not one §4.1 gives this station.
        """
        # Bound to the variant type the node was *declared* with, never to the runtime
        # type of `value`: PartCount, GoodCount and RejectCount arrive as `int`
        # through a parameter PEP 484 types `float`, so anything dispatching on
        # isinstance(value, float) would miss every counter in the line.
        await _write_at(self.historised[signal], self.types[signal], at, value)

    async def trigger_event(self, at: datetime, fields: dict[str, object]) -> None:
        """Fire this station's event type with `at` as its Time.

        `fields` must name exactly `events.EVENT_FIELDS`. Raises ValueError if it
        does not, or if this station has no event type.
        """
        generator = self.require_event_generator()
        declared = {name for name, _ in EVENT_FIELDS}
        if set(fields) != declared:
            # Not pedantry, and not merely a typo guard. EventGenerator reuses one
            # Event object across every trigger, so a field left out keeps the
            # *previous* event's value and is sent as if it were this one's -- a
            # quiet wrong answer with a defect class attached to the wrong serial.
            raise ValueError(
                f"{self.code} event fields {sorted(fields)} are not EVENT_FIELDS "
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
        await _write_at(self.level, ua.VariantType.UInt32, at, level)


@dataclass(frozen=True)
class AddressSpace:
    idx: int
    line: Node
    clock: Node
    clock_time: Node
    clock_phase: Node
    clock_speed: Node
    stations_folder: Node
    buffers_folder: Node
    stations: dict[str, StationNodeSet]
    buffers: dict[str, BufferNodeSet]
    event_type: Node

    # M1 handles. station_s3.py still owns catch-up and live production and still
    # addresses S3's two variables and its event generator directly, and M1's
    # historian still primes and historises exactly those; Task 7 folds both into the
    # Line and deletes station_s3.py, and these four go with it. They are properties
    # rather than fields so there is one S3 in this object, not a second alias of it
    # that could be built wrong.
    @property
    def s3(self) -> Node:
        return self.stations[INSPECTION_STATION].node

    @property
    def takt(self) -> Node:
        return self.stations[INSPECTION_STATION].historised["TaktTime"]

    @property
    def part_count(self) -> Node:
        return self.stations[INSPECTION_STATION].historised["PartCount"]

    @property
    def event_gen(self) -> EventGenerator:
        return self.stations[INSPECTION_STATION].require_event_generator()


def _initial_value(signal: str, variant_type: ua.VariantType) -> float | str:
    """What a variable declares before anything writes to it -- and historises once.

    Raises KeyError for a variant type with no declared zero, which is the right
    failure for a signal added to the tables above without deciding what it starts at.
    """
    return _INITIAL_OVERRIDES.get(signal, _INITIAL_BY_TYPE[variant_type])


async def build_address_space(
    server: Server, idx: int, buffer_capacity: int
) -> AddressSpace:
    """§4.1's tree under Objects/Line, on an initialised but unstarted `server`.

    `buffer_capacity` is `Settings.buffer_capacity` -- the same number the `Buffer`
    objects themselves are built with, so the `Capacity` node a gateway reads and the
    capacity the line actually enforces are one configured value, not two.
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

    # create_custom_event_type wants a list, not the tuple EVENT_FIELDS is defined
    # as elsewhere -- list() here, not a per-element rebuild.
    event_type = await server.create_custom_event_type(
        idx,
        "InspectionResultEventType",
        ua.ObjectIds.BaseEventType,
        list(EVENT_FIELDS),
    )

    stations_folder = await line.add_object(idx, "Stations")
    stations: dict[str, StationNodeSet] = {}
    for code, signals in STATION_SIGNALS.items():
        station = await stations_folder.add_object(idx, code)
        historised: dict[str, Node] = {}
        types: dict[str, ua.VariantType] = {}
        for name, variant_type in PACKML_SIGNALS + signals:
            historised[name] = await station.add_variable(
                idx, name, _initial_value(name, variant_type), variant_type
            )
            types[name] = variant_type

        event_gen: EventGenerator | None = None
        if code == INSPECTION_STATION:
            # ORDER MATTERS. get_event_generator() adds the GeneratesEvent reference
            # from the emitting node to the event type and sets its EventNotifier bit.
            # historize_node_event() later reads exactly those GeneratesEvent
            # references to decide which event types to historise -- so the generator
            # must exist first, or event history is silently created with no columns.
            #
            # station.nodeid, not station: get_event_generator's emitting_node is
            # typed NodeId | int. Its implementation does accept a Node
            # (event_generator.py isinstance-checks for one), but that path is missing
            # from the public signature -- passing the NodeId reaches the identical
            # Node internally and satisfies mypy strict either way.
            event_gen = await server.get_event_generator(event_type, station.nodeid)

        stations[code] = StationNodeSet(code, station, historised, types, event_gen)

    buffers_folder = await line.add_object(idx, "Buffers")
    buffers: dict[str, BufferNodeSet] = {}
    for buffer_id, upstream, downstream in BUFFERS:
        buffer = await buffers_folder.add_object(idx, buffer_id)
        buffers[buffer_id] = BufferNodeSet(
            buffer_id,
            buffer,
            await buffer.add_variable(idx, "Level", 0, ua.VariantType.UInt32),
            # The static three. Written here by add_variable's own initial value and
            # never again, which is what makes them readable on connect without the
            # plant running -- §4.1's "topology is discovered, not configured".
            await buffer.add_variable(
                idx, "Capacity", buffer_capacity, ua.VariantType.UInt32
            ),
            await buffer.add_variable(
                idx, "UpstreamStation", upstream, ua.VariantType.String
            ),
            await buffer.add_variable(
                idx, "DownstreamStation", downstream, ua.VariantType.String
            ),
        )

    return AddressSpace(
        idx,
        line,
        clock,
        clock_time,
        clock_phase,
        clock_speed,
        stations_folder,
        buffers_folder,
        stations,
        buffers,
        event_type,
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
