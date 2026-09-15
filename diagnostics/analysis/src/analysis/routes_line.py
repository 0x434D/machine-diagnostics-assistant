"""§5.3's `/line/status` — and §2.2's claim, made checkable.

The architecture exists to support one sentence: *the diagnostics stack answers with the
plant stack shut down*. Every other endpoint takes a window and is therefore honest for
free — a window in the past reads the same whether the plant is running or not. This one
does not. "What is happening right now" over a database that stopped receiving six hours ago
is the one place where an answer can be confidently, silently wrong.

So nothing here is presented as current. Every value is the *last known* one, every one of
them carries the instant it was published at, and `staleness_seconds` is the single number
that says how far behind the whole picture is. With the plant down this endpoint reports a
stale line; it does not report a quiet one.
"""

from __future__ import annotations

from fastapi import APIRouter

from analysis import queries
from analysis.db import connection
from analysis.dependencies import NowDep, SettingsDep
from analysis.models import LineStatus

router = APIRouter()


@router.get("/line/status", operation_id="lineStatus")
def line_status(settings: SettingsDep, now: NowDep) -> LineStatus:
    """Each station's last state and reason, the buffer levels, the standing alarms, the
    last part out — and how old all of that is.

    `live` is a comparison and nothing more: the newest row in the database against
    `live_within_seconds`, both of which are in the response so that the verdict can be
    checked rather than believed.

    Two edges worth knowing. A database with no rows at all reports a null staleness and
    `live` false — nothing has ever arrived, which is a different thing from nothing having
    arrived lately and calls for a different action. And a *negative* staleness is possible
    and is reported as it is: all analysis reads `SourceTimestamp`, which is simulated time,
    and simulated time ahead of the wall clock is a fact about the plant's clock rather than
    something to clamp away.
    """
    with connection(settings) as conn:
        latest = queries.latest_data_at(conn)
        stations = queries.latest_station_states(conn)
        buffers = queries.latest_buffer_levels(conn)
        alarms = queries.active_alarms(conn)
        last_out = queries.last_part_out(conn)

    staleness = None if latest is None else (now - latest).total_seconds()
    return LineStatus(
        as_of=now,
        latest_data_at=latest,
        staleness_seconds=staleness,
        live=staleness is not None
        and staleness <= settings.line_status_live_within_seconds,
        live_within_seconds=settings.line_status_live_within_seconds,
        stations=stations,
        buffers=buffers,
        active_alarms=alarms,
        last_part_out=last_out,
    )
