"""Objects/Line/Clock, Objects/Line/Stations/S3_Inspection, per §4.1, narrowed to M1.

M1 carries TaktTime and PartCount -- §4.1's two variables for S3 -- and the
inspection event. They are deliberately different in kind: TaktTime is a noisy
float where a deadband is meaningful, PartCount a monotonic counter where a
deadband would silently lose parts. The pair tests the gateway's per-signal
deadband configuration in both directions.

Clock was in §4.1 from the start but had no code here until this module grew it:
Task 4's plan listed only S3's two variables and the inspection event, and its
"what M1 leaves off" discussion never mentions the clock -- dropped by omission,
not by decision. It matters because §4.3's handshake reads Clock.Phase to tell
catch-up from live, which the gateway cannot do without it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from asyncua import Node, Server, ua
from asyncua.server import EventGenerator

from simulator.clock import Phase, SimulatedClock
from simulator.events import EVENT_FIELDS


@dataclass
class AddressSpace:
    idx: int
    line: Node
    clock: Node
    clock_time: Node
    clock_phase: Node
    clock_speed: Node
    stations: Node
    s3: Node
    takt: Node
    part_count: Node
    event_type: Node
    event_gen: EventGenerator


# Written once at build time, before publish_clock's first tick has run (that tick
# starts in the same TaskGroup as simulator.status.publish -- see server.main, and
# follows within one status_interval_seconds of boot). The epoch is a self-evidently
# bootstrap instant, not a real one, and not a call to datetime.now(): nothing outside
# clock.py's own injected wall_fn may read the real clock (see clock.SimulatedClock.wall).
_BOOTSTRAP_TIME = datetime(1970, 1, 1, tzinfo=UTC)


async def build_address_space(server: Server, idx: int) -> AddressSpace:
    objects = server.nodes.objects
    line = await objects.add_object(idx, "Line")

    # Clock is a sibling of Stations (§4.1), not a station itself: it exposes the
    # line's own timebase, not something S3 measures. Deliberately never historised
    # (see publish_clock) -- R1 reconciles exactly three streams (TaktTime, PartCount,
    # the inspection event) against the ledger, and these three nodes have no ledger
    # entry to reconcile: they are operational metadata the gateway reads live to tell
    # catch-up from live (§4.3), not process data anyone replays from history.
    clock = await line.add_object(idx, "Clock")
    clock_time = await clock.add_variable(
        idx, "SimulatedTime", _BOOTSTRAP_TIME, ua.VariantType.DateTime
    )
    clock_phase = await clock.add_variable(
        idx, "Phase", Phase.CATCHUP.value, ua.VariantType.String
    )
    clock_speed = await clock.add_variable(idx, "Speed", 1.0, ua.VariantType.Double)

    stations = await line.add_object(idx, "Stations")
    s3 = await stations.add_object(idx, "S3_Inspection")

    takt = await s3.add_variable(idx, "TaktTime", 0.0, ua.VariantType.Double)
    part_count = await s3.add_variable(idx, "PartCount", 0, ua.VariantType.UInt32)

    # create_custom_event_type wants a list, not the tuple EVENT_FIELDS is defined
    # as elsewhere -- list() here, not a per-element rebuild.
    event_type = await server.create_custom_event_type(
        idx,
        "InspectionResultEventType",
        ua.ObjectIds.BaseEventType,
        list(EVENT_FIELDS),
    )

    # ORDER MATTERS. get_event_generator() adds the GeneratesEvent reference from
    # the emitting node to the event type and sets its EventNotifier bit.
    # historize_node_event() later reads exactly those GeneratesEvent references to
    # decide which event types to historise -- so the generator must exist first,
    # or event history is silently created with no columns.
    #
    # s3.nodeid, not s3: get_event_generator's emitting_node is typed NodeId | int.
    # Its implementation does accept a Node (event_generator.py isinstance-checks for
    # one), but that path is missing from the public signature -- passing the NodeId
    # reaches the identical Node internally and satisfies mypy strict either way.
    event_gen = await server.get_event_generator(event_type, s3.nodeid)

    return AddressSpace(
        idx,
        line,
        clock,
        clock_time,
        clock_phase,
        clock_speed,
        stations,
        s3,
        takt,
        part_count,
        event_type,
        event_gen,
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
    the Clock object for why R1's three-stream count depends on that.
    """
    while True:
        now, phase, speed = _clock_snapshot(clock)
        await space.clock_time.write_value(now, ua.VariantType.DateTime)
        await space.clock_phase.write_value(phase.value, ua.VariantType.String)
        await space.clock_speed.write_value(speed, ua.VariantType.Double)
        await asyncio.sleep(interval_seconds)
