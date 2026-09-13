"""The one tool M1 ships (§13), with coverage folded into it.

§5.3 lists /coverage as its own endpoint so the agent can ask whether it has data before
answering. Here it is part of the stats response instead, so §6.1's step-3 coverage check
cannot be skipped by forgetting to call a second endpoint. Splitting them out is M3's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from analysis.config import Settings
from analysis.db import connection
from analysis.dependencies import settings_dependency
from analysis.models import Coverage, DefectClassCount, Gap, InspectionStats, Window

router = APIRouter()


@router.get("/inspection/stats", operation_id="inspectionStats")
def inspection_stats(
    from_: Annotated[datetime, Query(alias="from")],
    to: Annotated[datetime, Query()],
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> InspectionStats:
    """Counts over a closed window, with the gaps that make them incomplete."""
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE result = 'reject') "
            "FROM inspection_results WHERE source_ts >= %s AND source_ts < %s",
            (from_, to),
        )
        total, rejects = cur.fetchone() or (0, 0)

        cur.execute(
            "SELECT defect_class, count(*) FROM inspection_results "
            "WHERE source_ts >= %s AND source_ts < %s AND defect_class IS NOT NULL "
            "GROUP BY defect_class ORDER BY count(*) DESC",
            (from_, to),
        )
        by_class = [
            DefectClassCount(defect_class=row[0], count=row[1])
            for row in cur.fetchall()
        ]

        cur.execute(
            "SELECT assembly_serial FROM inspection_results "
            "WHERE source_ts >= %s AND source_ts < %s AND result = 'reject' "
            "ORDER BY source_ts LIMIT %s",
            (from_, to, settings.sample_serial_limit),
        )
        samples = [row[0] for row in cur.fetchall()]

        # Overlap, not containment: a gap that starts before the window and ends inside it
        # still makes the window incomplete.
        cur.execute(
            "SELECT from_ts, to_ts, reason FROM ingest_gaps "
            "WHERE from_ts < %s AND to_ts > %s ORDER BY from_ts",
            (to, from_),
        )
        gaps = [
            Gap(from_ts=row[0], to_ts=row[1], reason=row[2]) for row in cur.fetchall()
        ]

    return InspectionStats(
        window=Window(from_ts=from_, to_ts=to),
        total=total,
        rejects=rejects,
        by_defect_class=by_class,
        sample_serials=samples,
        coverage=Coverage(gaps=gaps),
    )
