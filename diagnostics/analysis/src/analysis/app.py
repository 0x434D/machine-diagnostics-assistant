"""The analysis service.

§5.3: a separate deployable computing on demand, not a background worker writing result
tables. With a live plant, materialised analysis is permanently stale and "what is happening
right now" becomes structurally unanswerable.
"""

from __future__ import annotations

from auth.requests import principal
from fastapi import Depends, FastAPI

from analysis import (
    routes_alarms,
    routes_coverage,
    routes_inspection,
    routes_knowledge,
    routes_line,
    routes_parts,
    routes_patterns,
    routes_signals,
    routes_stops,
    routes_time,
    routes_traceability,
)

app = FastAPI(
    title="machine-agent analysis",
    version="0.1.0",
    # §10.5, and the reason it is here rather than on each router: an endpoint added
    # tomorrow is closed by the line that closed the ones added today. A per-route
    # dependency is a decision somebody has to remember to repeat, and the endpoint nobody
    # remembered is the one §1.8 is about.
    dependencies=[Depends(principal)],
    # **The three routes FastAPI would add for itself, deliberately not served.** They are
    # not APIRoutes and an application-wide dependency does not reach them, so keeping them
    # would mean four unauthenticated endpoints describing every other one. The schema is
    # committed in `contracts/analysis.openapi.yaml` and generated from `app.openapi()` by
    # `make contract`, so a running service serves nothing the repository does not already
    # hold -- which makes this a subtraction rather than a loss.
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
app.include_router(routes_time.router)
app.include_router(routes_coverage.router)
app.include_router(routes_stops.router)
app.include_router(routes_alarms.router)
app.include_router(routes_signals.router)
app.include_router(routes_line.router)
app.include_router(routes_inspection.router)
app.include_router(routes_patterns.router)
app.include_router(routes_knowledge.router)
# **Before `routes_parts`, and it has to be.** Starlette matches routes in registration
# order, and `/parts/{serial}` matches `/parts/affected` with `serial="affected"` — so the
# containment endpoint registered after it would be unreachable, answering 404 for a part
# named "affected" instead. Nothing in either module hints at the coupling; this line is it.
app.include_router(routes_traceability.router)
app.include_router(routes_parts.router)
