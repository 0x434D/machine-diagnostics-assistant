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
from analysis.queries_traceability import Anchor, PartSelection

router = APIRouter()

_WINDOW_SELECTS: dict[Anchor, str] = {
    "created": (
        "parts whose assembly was created at the head of the line inside the window. Not "
        "parts pressed inside it: the press records its numbers against the serial and no "
        "instant beside them, and the two differ by however long the part sat in B1_2"
    ),
    "inspected": (
        "parts inspected inside the window. Not parts pressed inside it \u2014 the two differ "
        "by the S2\u2192S3 transit, which buffers and takt jitter make variable, and treating "
        "them as the same instant is the approximation \u00a73.4a rejects"
    ),
    "left": (
        "parts dispositioned at the tail of the line inside the window. Not parts pressed "
        "inside it \u2014 the two differ by the S2\u2192S4 transit, which spans two buffers"
    ),
}
"""What the window selected, said in words, one sentence per anchor.

In the response rather than only in a docstring, because the distinction it carries is the
one a reader assumes away: asked *\u201cwhich parts passed S2 while the force was out of
tolerance\u201d* and handed a list, nobody checks which instant the window was applied to. The
value criterion is exact and the window is not the press's; saying so is what keeps the
answer from being read as more than it is.
"""


@router.get("/parts/affected", operation_id="affectedParts")
def affected_parts(
    window: WindowDep,
    settings: SettingsDep,
    station: Annotated[str | None, Query()] = None,
    carrier: Annotated[int | None, Query()] = None,
    lot: Annotated[str | None, Query()] = None,
    defect_class: Annotated[str | None, Query()] = None,
    signal: Annotated[str | None, Query()] = None,
    below: Annotated[float | None, Query()] = None,
    above: Annotated[float | None, Query()] = None,
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

    **`signal` with `below` and/or `above` is §5.3's worked example, answered.** *"Which
    parts passed S2 while the joining force was out of tolerance?"* cannot be asked as a
    window on S2, and it does not need to be: the force is recorded per part, at the instant
    of production, and a part whose own `PeakForce` is out of tolerance is out of tolerance
    whatever the clock was doing. So the criterion is a value comparison against the part's
    own record, with no time range anywhere near it, and the window then bounds when those
    parts were *completed* rather than when they were pressed — which `window_selects` says
    in the response, because it is a difference a reader would otherwise assume away.

    404 for a station the line does not have, 422 for one that records no instant or for
    half a tolerance, and an empty scope for criteria that simply matched nothing. Three
    different answers, because they call for three different next steps (§6.5).
    """
    if (signal is None) != (below is None and above is None):
        raise HTTPException(
            status_code=422,
            detail=(
                "signal and a tolerance bound must be given together: a signal with no "
                "bound selects every part that has one, and a bound with no signal has "
                "nothing to compare. Either half alone would return a scope that looks "
                f"like an answer; got signal={signal!r}, below={below!r}, above={above!r}"
            ),
        )

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
            signal=signal,
            below=below,
            above=above,
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
            signal=signal,
            below=below,
            above=above,
            anchor=anchor,
            window_selects=_WINDOW_SELECTS[anchor],
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
