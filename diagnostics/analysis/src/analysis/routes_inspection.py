"""§5.3's `/inspection/stats`: the counts, the breakdown, the coverage and the groupings.

M1 shipped this as the one tool with coverage folded in, and left both splits to M3. Both
are done, and neither by duplication: `/coverage` and this endpoint's `coverage` field come
from `routes_coverage.coverage_of`, and `by_defect_class` and `group_by=defect_class` come
from `queries.defect_class_counts`. One implementation behind two exposures each, because
the one thing worse than a missing endpoint is two endpoints that disagree.

The coverage field stays folded in even now that `/coverage` exists, for M2b's reason: §6.1's
step-3 coverage check must not be skippable by forgetting to call a second endpoint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from psycopg import Connection

from analysis import queries, windows
from analysis.config import Settings
from analysis.db import connection
from analysis.dependencies import SettingsDep, WindowDep
from analysis.models import (
    DefectClassCount,
    GroupBy,
    InspectionStats,
    StatsGroup,
    Window,
)
from analysis.routes_coverage import coverage_of

router = APIRouter()


@router.get("/inspection/stats", operation_id="inspectionStats")
def inspection_stats(
    window: WindowDep,
    settings: SettingsDep,
    group_by: Annotated[GroupBy | None, Query()] = None,
) -> InspectionStats:
    """Counts over a half-open window, with the gaps that make them incomplete.

    **The breakdown reads §3.4's score vector, not a scalar class.** It grouped by
    `inspection_results.defect_class` until M2b Task 7, and by then nothing filled that
    column: the plant sends `DefectClasses`, the gateway's select clause named the old
    scalar, asyncua maps a select clause it cannot resolve to `Variant(None)` rather than
    erroring, the writer resolves that to `DBNull`, and `defect_class IS NOT NULL` then
    emptied the group-by. No exception and no 500 — `by_defect_class: []` beside a correct
    `total` and `rejects`, which is a silently empty answer to "which defects are we
    seeing" and the exact failure §1 exists to prevent. The column is no longer in the read
    layer at all, so the query that produced that answer can no longer be written.

    `group_by` is absent by default and `groups` is then null rather than empty: a caller
    that asked for no grouping and a caller that asked for one over an empty window are not
    entitled to the same answer.
    """
    with connection(settings) as conn:
        total, rejects = queries.inspection_totals(conn, window)
        class_counts = queries.defect_class_counts(
            conn, window, settings.defect_class_threshold
        )
        return InspectionStats(
            window=Window.of(window),
            total=total,
            rejects=rejects,
            by_defect_class=[
                DefectClassCount(defect_class=name, count=count)
                for name, count in class_counts
            ],
            defect_class_threshold=settings.defect_class_threshold,
            rejects_without_class=queries.rejects_without_class(
                conn, window, settings.defect_class_threshold
            ),
            sample_serials=queries.reject_sample_serials(
                conn, window, settings.sample_serial_limit
            ),
            coverage=coverage_of(conn, window),
            group_by=group_by,
            groups=(
                None
                if group_by is None
                else _groups(conn, window, group_by, settings, total, class_counts)
            ),
        )


def _groups(
    conn: Connection,
    window: windows.Window,
    group_by: GroupBy,
    settings: Settings,
    total: int,
    class_counts: list[tuple[str, int]],
) -> list[StatsGroup]:
    """One group per value of the requested dimension.

    `class_counts` is passed in rather than re-queried: it is the same list
    `by_defect_class` is built from, and a second query would be a second chance for the two
    halves of one response to disagree about what a defect-class count counts.

    What `parts` means differs by dimension and `StatsGroup` is where that is spelled out.
    The short of it: for `defect_class` the denominator is every inspected part in the
    window, because a class is not a subset of parts.
    """
    if group_by == "time":
        return [
            StatsGroup(
                key=bucket_from.isoformat(),
                from_ts=clipped_from,
                to_ts=clipped_to,
                parts=parts,
                rejects=rejects,
                reject_share=_share(rejects, parts),
            )
            for bucket_from, clipped_from, clipped_to, parts, rejects in (
                queries.counts_by_time_bucket(
                    conn, window, int(settings.stats_time_bucket.total_seconds())
                )
            )
        ]
    if group_by == "defect_class":
        return [
            StatsGroup(
                key=name,
                from_ts=None,
                to_ts=None,
                parts=total,
                rejects=count,
                reject_share=_share(count, total),
            )
            for name, count in class_counts
        ]
    rows = (
        queries.counts_by_carrier(conn, window)
        if group_by == "carrier"
        else queries.counts_by_lane(conn, window)
    )
    return [
        StatsGroup(
            key=key,
            from_ts=None,
            to_ts=None,
            parts=parts,
            rejects=rejects,
            reject_share=_share(rejects, parts),
        )
        for key, parts, rejects in rows
    ]


def _share(rejects: int, parts: int) -> float | None:
    """The reject share, or None when there is nothing to take a share of.

    Never 0.0 for an empty group. "No parts, so no rate" and "parts, none of them rejected"
    are different facts and the agent says different things about them — an empty hour
    flattened to 0 % is a line reported as running perfectly while it was stopped.
    """
    return None if parts == 0 else rejects / parts
