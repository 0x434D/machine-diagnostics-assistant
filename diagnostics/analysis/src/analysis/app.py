"""The analysis service.

§5.3: a separate deployable computing on demand, not a background worker writing result
tables. With a live plant, materialised analysis is permanently stale and "what is happening
right now" becomes structurally unanswerable.
"""

from __future__ import annotations

from fastapi import FastAPI

from analysis import routes_inspection, routes_parts

app = FastAPI(title="machine-agent analysis", version="0.1.0")
app.include_router(routes_inspection.router)
app.include_router(routes_parts.router)
