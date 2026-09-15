"""§4.1's five event types and §4.2's alarm: what each carries, and in which order.

Kept apart from address_space.py: these field tables are the wire format the gateway
decodes, independent of how the address space wires an event type into the server.

**The field NAMES are the format; the order is each side's own.** A notification does
arrive as a positional `EventFieldList` matching the subscriber's SelectClauses, but
both ends address by name on the way in and out: `to_event_fields` resolves each clause
with `getattr(self, name)`, `trigger_event` assigns by name, and the gateway builds its
SelectClauses from the same list it then decodes positionally against. So reordering a
table here re-orders the request and the decode together and mis-assigns nothing.

What the two sides must agree on is the **set of names**, and the failure is quiet in
exactly one direction: `to_event_fields` answers a clause it cannot resolve with
`ua.Variant(None)` and no error at all (asyncua `common/events.py`), so a field renamed
or dropped on one side alone becomes a column that is null for ever with nothing raised.
The same silence covers a value published under the wrong key -- `lot.supplier` passed
as `"LotCode"` is a String in a String field and reaches Postgres unremarked.

The gateway states this list independently and deliberately -- a check that moves when
the thing it checks moves proves nothing -- so a change here is a change in two
repositories. `test_authenticity.py` is what holds the two against each other.

**One station, one event table.** asyncua historises events per *emitting node*, not
per event type: S1's two types share one table holding the union of their fields, and
a row carries NULL in the columns of the type it is not. Two event types on one
station must therefore never declare the same field name -- `HistorySQLite`
de-duplicates fields by node rather than by name, so two same-named property nodes
become two identical columns and the CREATE TABLE that fails on them is swallowed by
its own `except aiosqlite.Error`, leaving that station with no event history and no
error. S1's two tables below share nothing but what BaseEventType gives them, and
`ALARM` -- which every station emits -- prefixes every one of its fields for exactly
this reason.

**The instant is `Time`, not a field of our own.** Every event carries BaseEventType's
`Time`, which `StationNodeSet.trigger_event` sets to the simulated instant of the
cycle, and which the gateway already reads as the event's SourceTimestamp. §5.2's
`components.read_at` and `assemblies.created_at` are that instant; a `ReadAt` field
beside it would be a second copy of one fact, free to disagree with the first, which
is the same argument D12 makes for the three live-only nodes.

**Arrays are declared as scalar properties.** `Server.create_custom_event_type` takes
`(name, VariantType)` pairs and has no ValueRank parameter, so `ComponentSerials`,
`Curve`, `DefectClasses` and `Confidences` are declared scalar on the type and carry
an array at runtime. The Variant that goes on the wire carries its own array flag,
which is what every consumer decodes; the declared rank is read by nobody here.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from asyncua import ua


class EventType(NamedTuple):
    """One of §4.1's event types: its browse name, the stations that emit it, its fields.

    `name` is the OPC UA *type* browse name (`...EventType`), which is also what
    `HistorySQLite` records in `_EventTypeName` and what a client reads back from the
    event's own `EventType` field -- the only thing telling two event types apart once
    they share a station's table.

    `stations` is a tuple and not one name because §4.2's alarm is emitted by every
    station: an alarm carries the station that raised it (§5.2's `alarms.station_id`),
    and a type declared on one station makes that column a constant the schema pretends
    is a variable. The other five are one station each, which is what §4.1 gives them.
    """

    name: str
    stations: tuple[str, ...]
    fields: tuple[tuple[str, ua.VariantType], ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.fields)


COMPONENT_READ: Final = EventType(
    "ComponentReadEventType",
    ("S1_Feeding",),
    (
        ("ComponentSerial", ua.VariantType.String),
        ("Lane", ua.VariantType.UInt32),
        ("LotCode", ua.VariantType.String),
        ("Supplier", ua.VariantType.String),
    ),
)
"""One component off one lane (§3.1's asymmetric identity, component half).

**`Supplier` is beyond §4.1's "(component serial, lane, lot)" and is here on purpose.**
§5.2's `component_lots` row is `lot_code · lane · supplier · loaded_at · depleted_at`,
and D12 makes `Lane{n}_Lot` live-only -- so a supplier read off that node exists only
while the plant is up, and the diagnostics stack must answer with the plant shut down.
This event is the only path a supplier has into Postgres, and §6.4's audit trail is
what wants it. `loaded_at` and `depleted_at` need no field of their own: a lot is
loaded at the instant of its first draw and depleted at the first draw of the next lot
on that lane, both of which are `MIN(Time)` over this stream -- an exact group-by on
the lot code, not a time-range join.
"""

ASSEMBLY_CREATED: Final = EventType(
    "AssemblyCreatedEventType",
    ("S1_Feeding",),
    (
        ("AssemblySerial", ua.VariantType.String),
        ("ComponentSerials", ua.VariantType.String),
        ("CarrierId", ua.VariantType.UInt32),
    ),
)
"""The assembly S1 creates when it loads a carrier, and what went into it.

`ComponentSerials` is an array in `identity.LANES` order, because a component's index
in it is §5.2's `genealogy.position`. That ordering is the whole of the as-built
structure: it is recorded at the instant of production, which is §3.4a's rule, and it
is what a containment query walks.
"""

PART_PROCESSED: Final = EventType(
    "PartProcessedEventType",
    ("S2_Joining",),
    (
        ("AssemblySerial", ua.VariantType.String),
        ("Curve", ua.VariantType.Double),
        ("PeakForce", ua.VariantType.Double),
        ("JoiningDistance", ua.VariantType.Double),
    ),
)
"""§3.4a's press record, against the serial rather than against the clock.

`Curve` is forces in newtons, sampled uniformly across the ram's stroke; it carries no
axis of its own, and `Line/Press/StrokeLength` is the millimetre span that turns a
sample index into ram travel. `PeakForce` and `JoiningDistance` are the same two
numbers §4.1 publishes as S2's streams, repeated here because the per-part record is
authoritative for the part and the time series is not (§3.4a).
"""

INSPECTION_RESULT: Final = EventType(
    "InspectionResultEventType",
    ("S3_Inspection",),
    (
        ("AssemblySerial", ua.VariantType.String),
        ("CarrierId", ua.VariantType.UInt32),
        ("Disposition", ua.VariantType.String),
        ("DefectClasses", ua.VariantType.String),
        ("Confidences", ua.VariantType.Double),
        ("Confidence", ua.VariantType.Double),
        ("ModelVersion", ua.VariantType.String),
        ("Image", ua.VariantType.ByteString),
    ),
)
"""§3.4's verdict, widened from M1's single class and single score.

**`DefectClasses` and `Confidences` are parallel arrays over all six classes**, in the
classifier's own order, not a list of the classes found. §3.4 requires six independent
scores in [0, 1] that do not sum to 1: scenario 6 needs every score able to fall
together, which no distribution over six values can express, and scenarios 4 and 5 each
need two classes high on one part. A good part is six low scores, not an absent vector
-- so the six ride every event, including every good one, which is what makes "did the
scores decay across all classes" a question the history can answer at all.

The names ride beside the scores rather than being agreed privately: the defect
vocabulary already exists twice in this repository (`inspection.classifier` and
`simulator.inspection_client`, pinned equal by a test, because the two uv workspaces
may not import each other), and a third copy in the gateway with nothing keeping it
equal is how a vector gets decoded against the wrong key with no error anywhere. Six
short strings against a reject's image -- 110,056 B at the median of R4's 500-render
measurement at the shipped configuration (`measurements/r4-image-sizes.txt`), p99
110,419 B -- is not a cost worth optimising.

**`Confidence` is the scalar and is not one of the six.** §3.4 is explicit that it is
confidence in the OK/NOK *verdict*: a good part is confidently good while every class
scores low. Reading the vector as a distribution is the measured defect -- it reported
a good part as 27 % confident and ~30 % misaligned.

There is deliberately no singular `DefectClass`. §5.2's widened `inspection_results` is
`defect_classes · confidences · positions` with no scalar class in it, and the
classifier's own named reason for a reject reaches Postgres as `PartCompletedEvent`'s
`Reason` (§5.2's `part_dispositions.reason`).

Only a reject carries `Image` (§3.4), and only from here -- the other four event types
have no image field at all, which is what keeps event page sizes per type worth
setting (D4).
"""

PART_COMPLETED: Final = EventType(
    "PartCompletedEventType",
    ("S4_Outfeed",),
    (
        ("AssemblySerial", ua.VariantType.String),
        ("Disposition", ua.VariantType.String),
        ("Reason", ua.VariantType.String),
    ),
)
"""§5.2's `part_dispositions` row: how the part left the line, and why.

`Reason` is the classifier's named defect class for a reject and empty for a good
part. It is S3's verdict carried on the part rather than re-derived here, because the
part S4 sorts is the part S3 inspected and joining the two streams on "when was this
serial at S3" is the inference §3.4a forbids.
"""

ALARM: Final = EventType(
    "AlarmEventType",
    ("S1_Feeding", "S2_Joining", "S3_Inspection", "S4_Outfeed"),
    (
        ("AlarmCode", ua.VariantType.String),
        ("AlarmText", ua.VariantType.String),
        ("AlarmSeverity", ua.VariantType.UInt32),
        ("AlarmRaisedAt", ua.VariantType.DateTime),
        ("AlarmActive", ua.VariantType.Boolean),
        ("AlarmAcknowledged", ua.VariantType.Boolean),
    ),
)
"""§4.2's alarm: a **simple custom event type**, not OPC UA Alarms & Conditions.

The same decision PackML gets: correct semantics, no conformance claim. A&C is a large
specification, and what the diagnosis needs out of it is the six fields below.

**One event per lifecycle transition** -- raise, acknowledge, clear -- carrying the state
the alarm is in after it. `AlarmActive` and `AlarmAcknowledged` are therefore what says
which transition a row is: active and unacknowledged is the raise, active and
acknowledged is the acknowledgement, and inactive is the clear. That is the flags' plain
meaning and not an inference from which fields happen to be filled, which is the
distinction `PostgresWriter.WriteEventAsync` makes for the other five types.

**`AlarmRaisedAt` rides all three, and it is the alarm's identity rather than a second
copy of `Time`.** `Time` is the instant of *this* transition; the row in §5.2's `alarms`
is keyed on (station, code, raised instant), so an acknowledgement that arrives before
its own raise -- a backfill window read out of order, or a raise that fell before the
gateway's history horizon -- can still create the row it belongs to. That is the same
rule M2b's `003_m2b.sql` states for genealogy: the referenced row is created by whichever
event names it first.

**Every field is prefixed `Alarm`**, and that is load-bearing twice. Every station emits
this type beside its own, and two event types on one station may not share a field name
(see this module's docstring); and BaseEventType already carries `Severity`, `Message`
and `Time`, which an unprefixed `Severity` would collide with on the type itself.

**Every station declares it**, though only S2 has a condition today (`alarms`' force
tolerance). §5.2 keys an alarm on `station_id` and §3.7's screen lists alarms per
station: a type declared on one station would make "which station raised it" a constant
the schema pretends is a variable, and the station that gains the second condition would
then be a change to the address space rather than a change to one file.
"""

EVENT_TYPES: Final[tuple[EventType, ...]] = (
    COMPONENT_READ,
    ASSEMBLY_CREATED,
    PART_PROCESSED,
    INSPECTION_RESULT,
    PART_COMPLETED,
    ALARM,
)
"""All six, in line order with the line-wide alarm last. `build_address_space` creates
them in this order and `attach_historian` historises the stations that emit them."""


def event_types_for(station: str) -> tuple[EventType, ...]:
    """Every event type `station` emits, in `EVENT_TYPES` order. Empty for a station
    §4.1 gives none -- which, from M2b, is no station at all."""
    return tuple(event for event in EVENT_TYPES if station in event.stations)
