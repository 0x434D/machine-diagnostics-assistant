"""§6.1 step 2: the model reads "last night" out of a sentence, code computes what it means.

Date arithmetic across shift boundaries and DST is what models are unreliable at and code is
exact at, so the split is deliberate and this endpoint is the code half. It reads no
database: the calendar is not in Postgres.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from analysis.dependencies import NowDep
from analysis.models import TimeResolution, Window
from analysis.time_expressions import UNDERSTOOD_EXPRESSIONS, resolve

router = APIRouter()


@router.get("/time/resolve", operation_id="resolveTime")
def resolve_time(
    expression: Annotated[str, Query()],
    now: NowDep,
) -> TimeResolution:
    """Resolve a fixed time phrase to a concrete UTC window against the shift calendar.

    **An expression this does not understand is a 422 carrying the list of ones it does,
    never a guess.** A near-miss guessed wrong is the worst of the three possible outcomes:
    the caller gets a window, believes it is the one it asked for, and every number computed
    over it is an answer to a different question. The list is in the error so that the agent
    can retry with something real instead of rephrasing at random.
    """
    resolution = resolve(expression, now)
    if resolution is None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"no understood time expression matches {expression!r}",
                "understood": sorted(UNDERSTOOD_EXPRESSIONS),
            },
        )
    return TimeResolution(
        expression=expression,
        window=Window.of(resolution.window),
        label=resolution.label,
        closed=resolution.closed,
        now=now,
    )
