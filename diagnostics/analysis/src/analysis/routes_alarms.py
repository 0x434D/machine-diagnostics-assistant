"""§5.3's `/alarms` and `/alarms/{id}`: the lifecycle, not a list of raisings.

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
from analysis.models import Alarm, AlarmList, Window

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


@router.get("/alarms/{alarm_id}", operation_id="getAlarm")
def get_alarm(alarm_id: int, settings: SettingsDep) -> Alarm:
    """One alarm and its whole lifecycle, by the id a citation carries.

    **No window, and that is the entire reason this exists beside `/alarms`.** §7.3 resolves
    `{ kind: "alarm", id: 207 }` here, and a citation carries an id and no interval — so
    opening one out of the collection endpoint would mean guessing the window the alarm was
    raised in, and guessing wrong renders a real alarm as an unresolvable citation. §7.2: a
    citation you cannot open is barely a citation.

    404 when no alarm carries the id, and 422 when the id is not one — an alarm id is the
    `alarms.id` column, so a path segment that is not an integer names no row that could
    exist. §6.5 needs those to be different answers, and neither is an empty result.
    """
    with connection(settings) as conn:
        found = queries.alarm(conn, alarm_id)

    if found is None:
        raise HTTPException(status_code=404, detail=f"no alarm with id {alarm_id}")
    return found
