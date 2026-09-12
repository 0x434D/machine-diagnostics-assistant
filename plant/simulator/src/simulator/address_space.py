"""Objects/Line/Stations/S3_Inspection, per §4.1, narrowed to M1.

M1 carries TaktTime and PartCount -- §4.1's two variables for S3 -- and the
inspection event. They are deliberately different in kind: TaktTime is a noisy
float where a deadband is meaningful, PartCount a monotonic counter where a
deadband would silently lose parts. The pair tests the gateway's per-signal
deadband configuration in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass

from asyncua import Node, Server, ua
from asyncua.server import EventGenerator

from simulator.events import EVENT_FIELDS


@dataclass
class AddressSpace:
    idx: int
    line: Node
    stations: Node
    s3: Node
    takt: Node
    part_count: Node
    event_type: Node
    event_gen: EventGenerator


async def build_address_space(server: Server, idx: int) -> AddressSpace:
    objects = server.nodes.objects
    line = await objects.add_object(idx, "Line")
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
        idx, line, stations, s3, takt, part_count, event_type, event_gen
    )
