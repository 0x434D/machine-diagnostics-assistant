"""The containment queries, and the one rule they all obey: read the part, never the clock.

§3.4a: the per-part record is authoritative for the part and is never reconstructed by
joining the time series on "which part was at S2 at 02:14:07". Every predicate below keys
on a serial, a carrier id or a lot id; the only time range any of them applies is to an
instant the part itself carries. `tests/test_traceability.py` is what fails if that stops
being true.

**The window needs an instant, and a part has three of them.** §4.1 publishes a creation
event at the head of the line, a verdict at the inspection station and a disposition at the
tail — and nothing at all at the press, whose per-part record carries two numbers and no
time. So "passed a station during this window" is answerable at three of the four stations
and is not answerable at the fourth, and this module says so rather than reaching for the
nearest instant it can find. Answering the press with the creation instant would be a
containment list built on a guess, which is the one thing a containment list must not be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from psycopg import Connection

from analysis.models import Disposition, LotRef
from analysis.windows import Window

Anchor = Literal["created", "inspected", "left"]
"""Which per-part instant a window is applied to."""

_ANCHOR_COLUMN: dict[Anchor, str] = {
    "created": "a.created_at",
    "inspected": "r.source_ts",
    "left": "d.at",
}
"""The column each anchor reads. A fixed mapping over a `Literal`, so nothing a caller
sends reaches the SQL text — every caller-supplied value below travels as a parameter."""


@dataclass(frozen=True)
class PartSelection:
    """A containment scope: the criteria, and the instant the window is applied to.

    Criteria conjoin. An empty selection with a window is "every part made in this window",
    which is a legitimate scope and the one a shift-wide containment starts from.
    """

    window: Window | None = None
    anchor: Anchor = "created"
    carrier: int | None = None
    lot_code: str | None = None
    defect_class: str | None = None


def station_anchor(conn: Connection, station: str) -> Anchor | None:
    """Which per-part instant `station` records, or None when it records none.

    Discovered from the line rather than written down: the head of the line is the station
    no buffer discharges into and is where `assemblies.created_at` is written, the tail is
    the station no buffer draws from and is where a part is dispositioned, and the
    inspecting station is the one `inspection_results` names. A line with a fifth station
    needs a buffer row and nothing here.

    None is the press, and it is the honest answer: §5.2 records the press's numbers against
    the serial and no instant beside them, and `part_station_events` — the table that would
    have carried one — is empty by design because no event §4.1 publishes has a station
    entry or exit in it.

    Raises nothing for an unknown station code; the caller distinguishes "no such station"
    from "that station has no instant", because §6.5 needs a 404 and a 422 to mean
    different things.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM read.inspection_results i "
            "               JOIN read.stations s ON s.id = i.station_id "
            "               WHERE s.code = %(station)s), "
            "       EXISTS (SELECT 1 FROM read.stations s "
            "               WHERE s.code = %(station)s AND NOT EXISTS ("
            "                 SELECT 1 FROM read.buffers b "
            "                  WHERE b.downstream_station_id = s.id)), "
            "       EXISTS (SELECT 1 FROM read.stations s "
            "               WHERE s.code = %(station)s AND NOT EXISTS ("
            "                 SELECT 1 FROM read.buffers b "
            "                  WHERE b.upstream_station_id = s.id))",
            {"station": station},
        )
        row = cur.fetchone()
    if row is None:
        return None
    inspects, is_head, is_tail = row
    if inspects:
        return "inspected"
    if is_head:
        return "created"
    if is_tail:
        return "left"
    return None


def _clauses(
    selection: PartSelection, threshold: float
) -> tuple[str, str, dict[str, object]]:
    """The FROM and WHERE of a containment scope, and the parameters they carry.

    Built as text with named placeholders rather than interpolated: `anchor` indexes a
    fixed mapping and every other caller-supplied value below is a parameter, so there is
    no path from a query string into the SQL.
    """
    anchor = _ANCHOR_COLUMN[selection.anchor]
    source = (
        "read.assemblies a "
        "LEFT JOIN read.part_dispositions d ON d.assembly_serial = a.serial"
    )
    if selection.anchor == "inspected":
        source += " LEFT JOIN read.inspection_results r ON r.assembly_serial = a.serial"

    conditions: list[str] = []
    params: dict[str, object] = {}
    if selection.window is not None:
        conditions.append(f"{anchor} >= %(from_ts)s AND {anchor} < %(to_ts)s")
        params["from_ts"] = selection.window.from_ts
        params["to_ts"] = selection.window.to_ts
    if selection.carrier is not None:
        conditions.append("a.carrier_id = %(carrier)s")
        params["carrier"] = selection.carrier
    if selection.lot_code is not None:
        # Through the genealogy on the serial. The lot is a property of the component the
        # part was built from, recorded when the feeder read it — not of when the part was
        # made, which is what a time join would have used and what the staggered lot
        # boundaries (§3.5) exist to make wrong.
        conditions.append(
            "EXISTS (SELECT 1 FROM read.genealogy g "
            "        JOIN read.components c ON c.serial = g.component_serial "
            "        JOIN read.component_lots l ON l.id = c.lot_id "
            "        WHERE g.assembly_serial = a.serial "
            "          AND l.lot_code = %(lot_code)s)"
        )
        params["lot_code"] = selection.lot_code
    if selection.defect_class is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM read.inspection_results ri, "
            "        unnest(ri.defect_classes, ri.confidences) AS scored(c, score) "
            "        WHERE ri.assembly_serial = a.serial "
            "          AND scored.c = %(defect_class)s "
            "          AND scored.score >= %(threshold)s)"
        )
        params["defect_class"] = selection.defect_class
        params["threshold"] = threshold

    where = " AND ".join(conditions) if conditions else "true"
    return source, where, params


def select_parts(
    conn: Connection, selection: PartSelection, threshold: float
) -> list[tuple[str, str | None]]:
    """Every part in the scope, as `(assembly_serial, disposition)`.

    The rows come back whole and `containment.contain` splits them, rather than three
    capped queries with the split written into each one's WHERE: the rule for what counts
    as shipped would then live in three places, and the first of them to be edited alone
    would answer with a containment list that was quietly wrong. The cost is bounded by the
    line — one line, a 6 s takt, 33 h of history is some twenty thousand parts — and a
    scope wider than that is a question about a different plant.
    """
    source, where, params = _clauses(selection, threshold)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT a.serial, d.disposition FROM {source} WHERE {where}",
            params,
        )
        return [(row[0], row[1]) for row in cur.fetchall()]


def count_unplaceable(
    conn: Connection, selection: PartSelection, threshold: float
) -> int:
    """Parts matching every criterion whose anchor instant is null, so no window can place
    them.

    An assembly created before the gateway's history horizon has no creation instant — the
    one event carrying it arrived before the gateway did. Such a part is not outside the
    window; it is a part the window cannot be applied to, and dropping it silently is what
    shortens a containment list at the moment one is wanted.
    """
    windowless = PartSelection(
        window=None,
        anchor=selection.anchor,
        carrier=selection.carrier,
        lot_code=selection.lot_code,
        defect_class=selection.defect_class,
    )
    source, where, params = _clauses(windowless, threshold)
    anchor = _ANCHOR_COLUMN[selection.anchor]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {source} WHERE ({where}) AND {anchor} IS NULL",
            params,
        )
        row = cur.fetchone() or (0,)
    return int(row[0])


def lots_with_code(conn: Connection, lot_code: str) -> list[LotRef]:
    """Every `component_lots` row carrying this code.

    A list, because the code is unique per *lane* and not per line: the same code loaded on
    both feeders is two lots and two populations, and answering as though it were one would
    merge them — which is the distinction §3.5 relies on to keep scenario 7's "the defects
    correlate with the lot" answerable at all.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, lot_code, lane, supplier, loaded_at FROM read.component_lots "
            "WHERE lot_code = %s ORDER BY lane",
            (lot_code,),
        )
        return [
            LotRef(
                id=row[0],
                lot_code=row[1],
                lane=row[2],
                supplier=row[3],
                loaded_at=row[4],
            )
            for row in cur.fetchall()
        ]


@dataclass(frozen=True)
class ComponentRow:
    """One `components` row and whatever the genealogy has made of it so far."""

    lane: int | None
    read_at: datetime | None
    lot: LotRef | None
    assembly_serial: str | None
    assembly_created_at: datetime | None
    carrier_id: int | None


def component(conn: Connection, serial: str) -> ComponentRow | None:
    """The component, its lot and the assembly it went into — None if no such component.

    LEFT JOIN throughout. A component read at a feeder and not yet built into anything is
    the ordinary state of every component in the machine at any instant, and an inner join
    would turn that into "no such component", which §6.5 needs to mean something else.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.lane, c.read_at, l.id, l.lot_code, l.lane, l.supplier, l.loaded_at, "
            "       a.serial, a.created_at, a.carrier_id "
            "FROM read.components c "
            "LEFT JOIN read.component_lots l ON l.id = c.lot_id "
            "LEFT JOIN read.genealogy g ON g.component_serial = c.serial "
            "LEFT JOIN read.assemblies a ON a.serial = g.assembly_serial "
            "WHERE c.serial = %s "
            # Nothing in the schema stops one component appearing in two assemblies, and a
            # bare fetchone() over an unordered result would return a different one of them
            # on a different plan. Ordered, so a recall query cites the same assembly twice.
            "ORDER BY g.assembly_serial LIMIT 1",
            (serial,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    lot = (
        None
        if row[2] is None
        else LotRef(
            id=row[2], lot_code=row[3], lane=row[4], supplier=row[5], loaded_at=row[6]
        )
    )
    return ComponentRow(
        lane=row[0],
        read_at=row[1],
        lot=lot,
        assembly_serial=row[7],
        assembly_created_at=row[8],
        carrier_id=row[9],
    )


def carrier_exists(conn: Connection, carrier_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM read.carriers WHERE id = %s", (carrier_id,))
        return cur.fetchone() is not None


def part_disposition(conn: Connection, serial: str) -> Disposition | None:
    """How the part left the line, and why. None while it is still on it.

    One definition, read by `/parts/{serial}` and by `/components/{serial}/assembly`: a
    second copy of this four-line SELECT is a second place for "a good part sends an empty
    reason" to be handled differently.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT at, disposition, reason FROM read.part_dispositions "
            "WHERE assembly_serial = %s",
            (serial,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return Disposition(at=row[0], disposition=row[1], reason=row[2])
