"""§5.3's `/alarms`: the lifecycle, not a list of raisings.

An alarm has three instants and two of them are nullable, which is the whole of what makes
it useful: raised and never acknowledged is a different fact from raised, acknowledged and
still not cleared, and both are different from one that came and went inside the window.
A list that carried only `raised_at` would flatten the three into one.

**Nothing here feeds the propagation walk**, and that is the point rather than an omission —
see `models.Alarm`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from analysis import queries
from analysis.db import connection
from analysis.dependencies import SettingsDep, WindowDep
from analysis.models import AlarmList, Window

router = APIRouter()


@router.get("/alarms", operation_id="listAlarms")
def list_alarms(
    window: WindowDep,
    settings: SettingsDep,
    station: Annotated[str | None, Query()] = None,
) -> AlarmList:
    """Every alarm whose life overlaps the window, newest first.

    A `station` that the line does not have is a 404 rather than an empty list: §6.5 checks
    cited ids against the database, and "S9 raised no alarms" is a false statement about a
    station that does not exist.
    """
    with connection(settings) as conn:
        if station is not None and queries.station_id(conn, station) is None:
            raise HTTPException(status_code=404, detail=f"no station {station}")
        alarms = queries.alarms_overlapping(conn, window, station)

    return AlarmList(window=Window.of(window), station=station, alarms=alarms)
