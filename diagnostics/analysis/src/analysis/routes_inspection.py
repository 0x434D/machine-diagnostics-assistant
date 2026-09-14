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
    """Counts over a closed window, with the gaps that make them incomplete.

    **The breakdown reads §3.4's score vector, not a scalar class.** It grouped by
    `inspection_results.defect_class` until M2b Task 7, and by then nothing filled that
    column: the plant sends `DefectClasses`, the gateway's select clause named the old
    scalar, asyncua maps a select clause it cannot resolve to `Variant(None)` rather than
    erroring, the writer resolves that to `DBNull`, and `defect_class IS NOT NULL` then
    emptied the group-by. No exception and no 500 — `by_defect_class: []` beside a correct
    `total` and `rejects`, which is a silently empty answer to "which defects are we
    seeing" and the exact failure §1 exists to prevent.
    """
    with connection(settings) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE result = 'reject') "
            "FROM inspection_results WHERE source_ts >= %s AND source_ts < %s",
            (from_, to),
        )
        total, rejects = cur.fetchone() or (0, 0)

        # No filter on `result`, and none is needed: `inspection.classifier` boosts a class
        # only where the model believes it saw that defect, and any part with a boosted
        # class is a reject. So the threshold is the filter, and a part that scored high on
        # a class without being rejected would be a thing worth seeing rather than a thing
        # to hide.
        #
        # unnest of the two arrays together, so a class stays paired with its own score.
        # They are parallel by construction — the plant fills both from one dict and the
        # gateway writes both from one event — which is what makes the pairing safe here
        # rather than an assumption this query makes about them.
        cur.execute(
            "SELECT scored.defect_class, count(*) "
            "FROM inspection_results r, "
            "     unnest(r.defect_classes, r.confidences) AS scored(defect_class, score) "
            "WHERE r.source_ts >= %s AND r.source_ts < %s AND scored.score >= %s "
            "GROUP BY scored.defect_class "
            # The class name breaks ties, so two classes on the same count come back in a
            # stable order rather than in whichever order the plan happened to produce.
            "ORDER BY count(*) DESC, scored.defect_class",
            (from_, to, settings.defect_class_threshold),
        )
        by_class = [
            DefectClassCount(defect_class=row[0], count=row[1])
            for row in cur.fetchall()
        ]

        # What the breakdown above cannot explain. Removing the cause of the empty
        # `by_defect_class` did not remove the shape: a row written before the vector
        # existed carries no scores at all and contributes nothing to the group-by, and
        # §3.5 scenario 6 is a run where every score falls together and nothing crosses
        # the threshold. Both leave rejects unaccounted for, and counting them is what
        # turns "we saw no defects" into "we saw 30 rejects and could name none of them".
        #
        # NOT EXISTS over the same unnest, so this number and the breakdown are the two
        # halves of one rule rather than two rules that can drift. A NULL vector unnests
        # to no rows at all, which is why it lands here.
        cur.execute(
            "SELECT count(*) FROM inspection_results r "
            "WHERE r.source_ts >= %s AND r.source_ts < %s AND r.result = 'reject' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM unnest(r.defect_classes, r.confidences) AS scored(c, score)"
            "    WHERE scored.score >= %s)",
            (from_, to, settings.defect_class_threshold),
        )
        unclassified = (cur.fetchone() or (0,))[0]

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
        defect_class_threshold=settings.defect_class_threshold,
        rejects_without_class=unclassified,
        sample_serials=samples,
        coverage=Coverage(gaps=gaps),
    )
