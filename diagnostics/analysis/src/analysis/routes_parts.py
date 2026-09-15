"""The citation resolver, and §14's end-to-end trace of one serial.

§6.5 verifies every cited id against the database and §7.2 requires that clicking a citation
opens the underlying data. A citation you cannot open is barely a citation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import Connection

from analysis.config import Settings
from analysis.db import connection
from analysis.dependencies import settings_dependency
from analysis.models import (
    ComponentOrigin,
    Disposition,
    Inspection,
    Part,
    ProcessCurve,
    ProcessValue,
)

router = APIRouter()


@router.get("/parts/{serial}", operation_id="getPart")
def get_part(
    serial: str,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> Part:
    """§14's trace: genealogy, what each station recorded, the verdict and the disposition.

    **Every statement below is keyed by the serial and nothing else.** §3.4a: the per-part
    record is authoritative for the part and is never reconstructed by joining the time
    series on "which part was at S2 at 02:14:07" — with buffers, variable takt and history
    gaps that association is an inference, and inferred traceability is what makes a
    containment list unusable at the moment it matters. There is no time range anywhere in
    this function, and `tests/test_traceability.py` is what fails if one appears.

    "Station history" here is what happened to the part at each station — S1's creation
    instant, S2's press record, S3's verdict, S4's disposition — and not
    `part_station_events`, which §5.2 says has no source: no event the plant publishes
    carries a station entry or exit instant, and writing the *processing* instant into
    `entered_at` would be exactly the quiet wrong answer this system refuses.
    """
    with connection(settings) as conn:
        assembly = _assembly(conn, serial)
        inspection = _inspection(conn, serial)
        if assembly is None and inspection is None:
            # 404 rather than an empty object: citation verification depends on the
            # distinction between "this id does not resolve" and "this id resolves to
            # nothing" (§6.5).
            #
            # These two tables are the whole test. `genealogy`, `part_process_values`,
            # `part_process_curves` and `part_dispositions` all carry a foreign key to
            # `assemblies` and cannot hold a serial it does not; `inspection_results` is
            # the one per-part table with no such key, so it is the one that can know a
            # serial on its own.
            raise HTTPException(status_code=404, detail=f"no part {serial}")

        created_at, carrier_id = assembly if assembly is not None else (None, None)
        return Part(
            assembly_serial=serial,
            created_at=created_at,
            carrier_id=carrier_id,
            genealogy=_genealogy(conn, serial),
            process_values=_process_values(conn, serial),
            process_curves=_process_curves(conn, serial),
            inspection=inspection,
            disposition=_disposition(conn, serial),
        )


def _assembly(
    conn: Connection, serial: str
) -> tuple[datetime | None, int | None] | None:
    """The assembly row, whose two columns are both legitimately null.

    A null `created_at` is "this gateway never saw this assembly created", which happens on
    every boot to the parts that were already in the buffers, and is a fact about the part
    rather than a reason to withhold it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, carrier_id FROM read.assemblies WHERE serial = %s",
            (serial,),
        )
        row = cur.fetchone()
    return None if row is None else (row[0], row[1])


def _genealogy(conn: Connection, serial: str) -> list[ComponentOrigin]:
    """§3.4a's as-built structure, ordered by the position the creation event gave it.

    LEFT JOIN to the lot, not INNER: a component whose own read event lies before the
    history horizon has no lot, and an inner join would drop it from the assembly it is
    part of — silently shortening exactly the list a containment query is built on.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT g.component_serial, g.position, c.lane, c.read_at, "
            "       l.lot_code, l.supplier "
            "FROM read.genealogy g "
            "JOIN read.components c ON c.serial = g.component_serial "
            "LEFT JOIN read.component_lots l ON l.id = c.lot_id "
            "WHERE g.assembly_serial = %s "
            "ORDER BY g.position",
            (serial,),
        )
        return [
            ComponentOrigin(
                component_serial=row[0],
                position=row[1],
                lane=row[2],
                read_at=row[3],
                lot_code=row[4],
                supplier=row[5],
            )
            for row in cur.fetchall()
        ]


def _process_values(conn: Connection, serial: str) -> list[ProcessValue]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.code, v.signal, v.value "
            "FROM read.part_process_values v "
            "JOIN read.stations s ON s.id = v.station_id "
            "WHERE v.assembly_serial = %s "
            "ORDER BY s.code, v.signal",
            (serial,),
        )
        return [
            ProcessValue(station=row[0], signal=row[1], value=row[2])
            for row in cur.fetchall()
        ]


def _process_curves(conn: Connection, serial: str) -> list[ProcessCurve]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.code, c.signal, c.samples "
            "FROM read.part_process_curves c "
            "JOIN read.stations s ON s.id = c.station_id "
            "WHERE c.assembly_serial = %s "
            "ORDER BY s.code, c.signal",
            (serial,),
        )
        return [
            ProcessCurve(station=row[0], signal=row[1], samples=row[2])
            for row in cur.fetchall()
        ]


def _inspection(conn: Connection, serial: str) -> Inspection | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.source_ts, s.code, r.result, r.defect_classes, r.confidences, "
            "       r.confidence, r.model_version, i.assembly_serial IS NOT NULL "
            "FROM read.inspection_results r "
            "JOIN read.stations s ON s.id = r.station_id "
            "LEFT JOIN read.inspection_images i USING (assembly_serial) "
            "WHERE r.assembly_serial = %s",
            (serial,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return Inspection(
        source_ts=row[0],
        station=row[1],
        result=row[2],
        defect_classes=row[3],
        confidences=row[4],
        confidence=row[5],
        model_version=row[6],
        image_url=f"/parts/{serial}/image" if row[7] else None,
    )


def _disposition(conn: Connection, serial: str) -> Disposition | None:
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


@router.get(
    "/parts/{serial}/image",
    operation_id="getPartImage",
    responses={200: {"content": {"image/png": {}}}},
    response_class=Response,
)
def get_part_image(
    serial: str,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> Response:
    """Rejects only. A good part has no image and that is not a missing value (§3.4)."""
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT bytes FROM read.inspection_images WHERE assembly_serial = %s",
            (serial,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"no image for {serial}")

    return Response(content=bytes(row[0]), media_type="image/png")
