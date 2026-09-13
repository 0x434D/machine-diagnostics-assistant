"""The citation resolver.

§6.5 verifies every cited id against the database and §7.2 requires that clicking a citation
opens the underlying data. A citation you cannot open is barely a citation.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from analysis.config import Settings
from analysis.db import connection
from analysis.dependencies import settings_dependency
from analysis.models import Part

router = APIRouter()


@router.get("/parts/{serial}", operation_id="getPart")
def get_part(
    serial: str,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> Part:
    """Reads the per-part record directly.

    §3.4a: that record is authoritative for the part and is never reconstructed by joining
    the time series on "which part was at S3 at 02:14:07" — with buffers, variable takt and
    history gaps that association is an inference, and inferred traceability is what makes a
    containment list unusable at the moment it matters. There is no time-range join here.
    """
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT r.assembly_serial, r.source_ts, s.code, r.result, r.defect_class, "
            "       r.confidence, r.model_version, i.assembly_serial IS NOT NULL "
            "FROM inspection_results r "
            "JOIN stations s ON s.id = r.station_id "
            "LEFT JOIN inspection_images i USING (assembly_serial) "
            "WHERE r.assembly_serial = %s",
            (serial,),
        )
        row = cur.fetchone()

    if row is None:
        # 404 rather than an empty object: citation verification depends on the distinction
        # between "this id does not resolve" and "this id resolves to nothing" (§6.5).
        raise HTTPException(status_code=404, detail=f"no part {serial}")

    return Part(
        assembly_serial=row[0],
        source_ts=row[1],
        station=row[2],
        result=row[3],
        defect_class=row[4],
        confidence=row[5],
        model_version=row[6],
        image_url=f"/parts/{row[0]}/image" if row[7] else None,
    )


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
            "SELECT bytes FROM inspection_images WHERE assembly_serial = %s", (serial,)
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"no image for {serial}")

    return Response(content=bytes(row[0]), media_type="image/png")
