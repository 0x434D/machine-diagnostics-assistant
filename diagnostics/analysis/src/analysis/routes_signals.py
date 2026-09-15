"""§5.3's `/signals/trend`, aggregated in SQL.

An hour of one signal at §3.1's 6 s takt is six hundred rows and a shift is four thousand.
Averaging those in Python would move the whole history through this process to produce
twenty-four numbers, and would do it again for every caller — `avg`, `min` and `max`
computed next to the rows is the same answer without the transfer.

**The historised streams are not the per-part record.** §3.4a: what a station published at
02:14:07 is not what it recorded against the part that happened to be there, and this
endpoint answers only the first of those questions. `/parts/{serial}` answers the second, and
`tests/test_traceability.py` is what keeps the two apart.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Query

from analysis import queries
from analysis.db import connection
from analysis.dependencies import SettingsDep, WindowDep
from analysis.models import Aggregation, SignalTrend, Window

router = APIRouter()

_BUCKET_SECONDS: Final[dict[Aggregation, int | None]] = {
    "raw": None,
    "minute": 60,
    "hour": 3600,
}
"""What each `agg` means, which is not configuration: a minute is sixty seconds.

The *choice* of offering these three is §5.3's, and a fourth would be a contract change
rather than an environment variable — which is why this is a mapping over the same `Literal`
the query parameter is typed with, and not a `Settings` field that could be set to something
the type says is impossible.
"""


@router.get("/signals/trend", operation_id="signalTrend")
def signal_trend(
    window: WindowDep,
    settings: SettingsDep,
    station: Annotated[str, Query()],
    signal: Annotated[str, Query()],
    agg: Annotated[Aggregation, Query()] = "raw",
) -> SignalTrend:
    """One historised signal over a window, raw or bucketed.

    Buckets are half-open and aligned to the clock in UTC, so the 09:00 bucket is the same
    hour whichever window asked for it and two trends can be read against each other.

    A station the line does not have is a 404; a station that published nothing under this
    name is an empty series. §6.5 needs those to be different answers — "S2 has no signal
    called JoiningForce" and "S2's JoiningForce was silent for this hour" lead to different
    next steps, and an empty list for both would hide a typo as a measurement.
    """
    limit = settings.signal_point_limit
    bucket_seconds = _BUCKET_SECONDS[agg]

    with connection(settings) as conn:
        identifier = queries.station_id(conn, station)
        if identifier is None:
            raise HTTPException(status_code=404, detail=f"no station {station}")
        # One more row than the caller may have, which is what makes `truncated` a fact
        # rather than a guess from the count matching a limit the reader cannot see.
        points = (
            queries.raw_signal_points(conn, identifier, signal, window, limit + 1)
            if bucket_seconds is None
            else queries.bucketed_signal_points(
                conn, identifier, signal, window, bucket_seconds, limit + 1
            )
        )

    return SignalTrend(
        station=station,
        signal=signal,
        window=Window.of(window),
        agg=agg,
        bucket_seconds=bucket_seconds,
        points=points[:limit],
        truncated=len(points) > limit,
    )
