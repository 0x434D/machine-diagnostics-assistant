"""What every route takes from outside itself: the settings, the clock, and the window.

All three are FastAPI dependencies so that a test can replace them. The clock especially:
`/time/resolve` and `/line/status` are both statements *about* now, and a service that read
the wall clock inside the handler would have two endpoints whose answers cannot be
reproduced from their inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from analysis.config import Settings
from analysis.windows import Window


def settings_dependency() -> Settings:
    return Settings()


def now_dependency() -> datetime:
    """The injected clock. UTC and timezone-aware, which `Window` and `resolve` both
    require and neither will silently do without."""
    return datetime.now(UTC)


def _window(from_ts: datetime, to_ts: datetime) -> Window:
    """Build the half-open window, refusing the two shapes `Window` itself refuses.

    Checked here rather than caught from the constructor: both failures are a malformed
    request and belong to the caller, and a 422 that names which of the two it was is worth
    more than a 500 with a traceback. Nothing is recovered — the request does not proceed.

    A naive timestamp is rejected rather than assumed to be UTC. "2026-09-12T01:00:00" from
    a browser in Berlin and from one in Tokyo are nine hours apart, and a window quietly
    resolved against the wrong one of them answers a question nobody asked.
    """
    for name, value in (("from", from_ts), ("to", to_ts)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise HTTPException(
                status_code=422,
                detail=f"{name} must carry a timezone offset; got {value.isoformat()}",
            )
    if from_ts >= to_ts:
        raise HTTPException(
            status_code=422,
            detail=(
                "the window must run forwards and is half-open [from, to); got "
                f"from={from_ts.isoformat()} to={to_ts.isoformat()}"
            ),
        )
    return Window.utc(from_ts, to_ts)


def window_dependency(
    from_ts: Annotated[datetime, Query(alias="from")],
    to_ts: Annotated[datetime, Query(alias="to")],
) -> Window:
    """The `from`/`to` pair every §5.3 endpoint shares, as one validated interval."""
    return _window(from_ts, to_ts)


def optional_window_dependency(
    from_ts: Annotated[datetime | None, Query(alias="from")] = None,
    to_ts: Annotated[datetime | None, Query(alias="to")] = None,
) -> Window | None:
    """The same window, where §5.3 lists the endpoint without one.

    Half a window is a mistake, not a default: a caller that sent `from` alone meant to
    bound the answer and would otherwise get the whole history back believing it was
    bounded. So one without the other is a 422 rather than the other being invented.
    """
    if from_ts is None and to_ts is None:
        return None
    if from_ts is None or to_ts is None:
        raise HTTPException(
            status_code=422,
            detail="from and to must be given together, or neither of them",
        )
    return _window(from_ts, to_ts)


SettingsDep = Annotated[Settings, Depends(settings_dependency)]
NowDep = Annotated[datetime, Depends(now_dependency)]
WindowDep = Annotated[Window, Depends(window_dependency)]
OptionalWindowDep = Annotated[Window | None, Depends(optional_window_dependency)]
