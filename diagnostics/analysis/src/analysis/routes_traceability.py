"""§5.3's containment queries: *"`/parts/affected` is the query traceability exists for."*

*"Which parts passed S2 while the joining force was out of tolerance?"* → 340 serials, 62
rejected, **278 shipped and need checking.** That is the answer a plant needs at three in the
morning, and no amount of time-series analysis produces it — it needs the per-part record,
which is why §3.4a insists the record is written at the instant of production and never
reconstructed afterwards.

Every route here reads a serial, a carrier id or a lot id. The only time range any of them
applies is to an instant the part itself carries, and `queries_traceability` is where that
rule is stated and `tests/test_traceability.py` is what fails if it is broken.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection

from analysis import queries, queries_traceability
from analysis.config import Settings
from analysis.containment import Containment, OutcomeGroup, contain
from analysis.db import connection
from analysis.dependencies import OptionalWindowDep, SettingsDep, WindowDep
from analysis.models import (
    AffectedParts,
    AppliedCriteria,
    CarrierParts,
    ComponentAssembly,
    LotParts,
    PartGroup,
    PartsByOutcome,
    Window,
)
from analysis.queries_traceability import PartSelection

router = APIRouter()


@router.get("/parts/affected", operation_id="affectedParts")
def affected_parts(
    window: WindowDep,
    settings: SettingsDep,
    station: Annotated[str | None, Query()] = None,
    carrier: Annotated[int | None, Query()] = None,
    lot: Annotated[str | None, Query()] = None,
    defect_class: Annotated[str | None, Query()] = None,
) -> AffectedParts:
    """The containment scope: which parts a condition touched, and where each of them went.

    The criteria conjoin and any of them may be omitted; a window on its own is "every part
    made in this window", which is where a shift-wide containment starts.

    **`station` is answered from that station's own per-part record, or refused.** The head
    of the line records a creation instant, the inspection station a verdict instant and the
    tail a disposition instant — and the press records two numbers against the serial and no
    time at all. Asked for the press, this returns 422 rather than reaching for a
    neighbouring station's instant: a containment list built on "the part was probably there
    around then" is a list someone acts on, and §3.4a is explicit that the association is an
    inference the moment a buffer sits between the two stations.

    404 for a station the line does not have, 422 for one that records no instant, and an
    empty scope for criteria that simply matched nothing. Three different answers, because
    they call for three different next steps (§6.5).
    """
    with connection(settings) as conn:
        anchor: queries_traceability.Anchor = "created"
        if station is not None:
            if queries.station_id(conn, station) is None:
                raise HTTPException(status_code=404, detail=f"no station {station}")
            resolved = queries_traceability.station_anchor(conn, station)
            if resolved is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{station} records no per-part instant, so a window cannot be "
                        "applied to it: its per-part record carries values against the "
                        "serial and no time, and reconstructing one from when the part "
                        "was made would be an inference, not traceability (§3.4a)"
                    ),
                )
            anchor = resolved

        selection = PartSelection(
            window=window,
            anchor=anchor,
            carrier=carrier,
            lot_code=lot,
            defect_class=defect_class,
        )
        containment = _contain(conn, selection, settings)
        unplaceable = queries_traceability.count_unplaceable(
            conn, selection, settings.defect_class_threshold
        )

    return AffectedParts(
        window=Window.of(window),
        criteria=AppliedCriteria(
            station=station,
            carrier=carrier,
            lot_code=lot,
            defect_class=defect_class,
            anchor=anchor,
        ),
        parts=_by_outcome(containment),
        unplaceable=unplaceable,
    )


@router.get("/lots/{lot_code}/parts", operation_id="lotParts")
def lot_parts(
    lot_code: str, settings: SettingsDep, window: OptionalWindowDep
) -> LotParts:
    """Which assemblies contain a component from this lot — §3.5 scenario 7's containment.

    404 when no lot carries the code, which is not the same as a lot that has gone into
    nothing yet. `lots` is a list because the code is unique per lane and not per line.
    """
    with connection(settings) as conn:
        lots = queries_traceability.lots_with_code(conn, lot_code)
        if not lots:
            raise HTTPException(status_code=404, detail=f"no lot {lot_code}")
        containment = _contain(
            conn, PartSelection(window=window, lot_code=lot_code), settings
        )

    return LotParts(
        lot_code=lot_code,
        lots=lots,
        window=None if window is None else Window.of(window),
        parts=_by_outcome(containment),
    )


@router.get("/components/{serial}/assembly", operation_id="componentAssembly")
def component_assembly(serial: str, settings: SettingsDep) -> ComponentAssembly:
    """§5.3's single-component recall: a supplier finds a defect months later and gives you
    one serial.

    Only answerable because components are individually serialised (§3.3). A component that
    has been read at a feeder and not yet built into anything answers with a null assembly —
    a real state, and one a 404 would have misreported as an unknown component.
    """
    with connection(settings) as conn:
        row = queries_traceability.component(conn, serial)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no component {serial}")
        disposition = (
            None
            if row.assembly_serial is None
            else queries_traceability.part_disposition(conn, row.assembly_serial)
        )

    return ComponentAssembly(
        component_serial=serial,
        lane=row.lane,
        read_at=row.read_at,
        lot=row.lot,
        assembly_serial=row.assembly_serial,
        assembly_created_at=row.assembly_created_at,
        carrier_id=row.carrier_id,
        disposition=disposition,
    )


@router.get("/carriers/{carrier_id}/parts", operation_id="carrierParts")
def carrier_parts(
    carrier_id: int, settings: SettingsDep, window: OptionalWindowDep
) -> CarrierParts:
    """Which assemblies rode this carrier.

    §3.1 keeps the carriers in a closed loop, so a carrier comes round again — this is a
    list over however much history the window admits, never a list of one pass. That is also
    what makes carrier wear detectable at all: a carrier that passed once would leave no
    statistical signal to find.
    """
    with connection(settings) as conn:
        if not queries_traceability.carrier_exists(conn, carrier_id):
            raise HTTPException(status_code=404, detail=f"no carrier {carrier_id}")
        containment = _contain(
            conn, PartSelection(window=window, carrier=carrier_id), settings
        )

    return CarrierParts(
        carrier_id=carrier_id,
        window=None if window is None else Window.of(window),
        parts=_by_outcome(containment),
    )


def _contain(
    conn: Connection, selection: PartSelection, settings: Settings
) -> Containment:
    return contain(
        queries_traceability.select_parts(
            conn, selection, settings.defect_class_threshold
        ),
        serial_limit=settings.affected_serial_limit,
    )


def _by_outcome(containment: Containment) -> PartsByOutcome:
    """The three groups on the wire. Shared by all four endpoints above, so a containment
    answer means the same thing whichever question produced it."""
    return PartsByOutcome(
        total=containment.total,
        rejected=_group(containment.rejected),
        shipped=_group(containment.shipped),
        on_the_line=_group(containment.on_the_line),
    )


def _group(group: OutcomeGroup) -> PartGroup:
    return PartGroup(
        count=group.count, serials=list(group.serials), truncated=group.truncated
    )
