"""§5.3's `/coverage`, and the one function every other endpoint's coverage comes from.

M2b folded coverage into `/inspection/stats` and left splitting it out to M3, with the
warning that mattered: §6.1's step-3 coverage check must not be skippable by forgetting to
call a second endpoint. Both are true at once here — `/coverage` stands alone for the agent
that wants to ask before it answers, and `coverage_of` is what `/inspection/stats` and
`/inspection/patterns` embed, so there is one implementation and several exposures rather
than two definitions of what a covered window is.
"""

from __future__ import annotations

from fastapi import APIRouter
from psycopg import Connection

from analysis import queries, windows
from analysis.coverage import compute_coverage
from analysis.db import connection
from analysis.dependencies import SettingsDep, WindowDep
from analysis.models import Coverage, Gap, Window

router = APIRouter()


def coverage_of(conn: Connection, window: windows.Window) -> Coverage:
    """The coverage report for `window`: the gaps in it, how much of it they cost, and what
    it actually holds.

    The three facts are kept apart because the failure this endpoint exists to prevent is
    reading them as one. An empty window that is fully covered is a quiet line; an empty
    window with a gap across it is a blackout; and an empty window with a gap across half of
    it is a number that must not be quoted as a rate. Without `observed` the first two are
    the same response, which is precisely §4.4's "missing data is indistinguishable from a
    quiet machine".
    """
    report = compute_coverage(window, queries.gaps_overlapping(conn, window))
    return Coverage(
        window=Window.of(window),
        gaps=[
            Gap(from_ts=gap.from_ts, to_ts=gap.to_ts, reason=gap.reason)
            for gap in report.gaps
        ],
        covered_fraction=report.covered_fraction,
        fully_covered=report.fully_covered,
        observed=queries.observed_span(conn, window),
    )


@router.get("/coverage", operation_id="coverage")
def coverage(window: WindowDep, settings: SettingsDep) -> Coverage:
    """Where the data is, and where it is not, over a half-open window."""
    with connection(settings) as conn:
        return coverage_of(conn, window)
