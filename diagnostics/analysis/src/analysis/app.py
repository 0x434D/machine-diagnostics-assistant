"""The analysis service.

§5.3: a separate deployable computing on demand, not a background worker writing result
tables. With a live plant, materialised analysis is permanently stale and "what is happening
right now" becomes structurally unanswerable.
"""

from __future__ import annotations

from fastapi import FastAPI

from analysis import (
    routes_alarms,
    routes_coverage,
    routes_inspection,
    routes_line,
    routes_parts,
    routes_patterns,
    routes_signals,
    routes_stops,
    routes_time,
    routes_traceability,
)

app = FastAPI(title="machine-agent analysis", version="0.1.0")
app.include_router(routes_time.router)
app.include_router(routes_coverage.router)
app.include_router(routes_stops.router)
app.include_router(routes_alarms.router)
app.include_router(routes_signals.router)
app.include_router(routes_line.router)
app.include_router(routes_inspection.router)
app.include_router(routes_patterns.router)
# **Before `routes_parts`, and it has to be.** Starlette matches routes in registration
# order, and `/parts/{serial}` matches `/parts/affected` with `serial="affected"` — so the
# containment endpoint registered after it would be unreachable, answering 404 for a part
# named "affected" instead. Nothing in either module hints at the coupling; this line is it.
app.include_router(routes_traceability.router)
app.include_router(routes_parts.router)
