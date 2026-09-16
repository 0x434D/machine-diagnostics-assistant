"""The fake analysis service the agent tests run against.

A fake is right here and a container would be wrong: these tests are about the pipeline's
behaviour when the data says a particular thing, and the queries themselves are already
tested against real Postgres in the analysis package.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import httpx
from agent.tools import Window

WINDOWS: dict[str, Window] = {
    "last hour": Window(
        start=datetime(2026, 9, 12, 13, 30, tzinfo=UTC),
        end=datetime(2026, 9, 12, 14, 30, tzinfo=UTC),
        label="last hour 2026-09-12 13:30 – 2026-09-12 14:30 UTC",
        closed=True,
    ),
    "this shift": Window(
        start=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
        end=datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        label="late shift 2026-09-12 14:00 – 2026-09-12 22:00 Europe/Berlin",
        closed=False,
    ),
    "last night": Window(
        start=datetime(2026, 9, 11, 20, 0, tzinfo=UTC),
        end=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
        label="night shift 2026-09-11 22:00 – 2026-09-12 06:00 Europe/Berlin",
        closed=True,
    ),
}


class FakeAnalysis:
    """Stands in for AnalysisClient.

    Knows one part by default, so a citation either resolves or does not — the distinction
    §6.5 depends on. Every call is recorded in `calls`, in order, because several of the
    pipeline's obligations are about *which step ran and when*, not about what it returned.
    """

    def __init__(
        self,
        responses: Mapping[str, object] | None = None,
        *,
        known: set[str] | None = None,
        windows: Mapping[str, Window] | None = None,
        errors: Mapping[str, str] | None = None,
        flaky: Mapping[str, str] | None = None,
        flaky_raises: set[str] | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._known = known if known is not None else {"A-00000007"}
        self._windows = dict(windows if windows is not None else WINDOWS)
        self._errors = dict(errors or {})
        self._flaky = dict(flaky or {})
        self._flaky_raises = set(flaky_raises or ())
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.windows: list[tuple[datetime, datetime]] = []

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def resolve_time(self, expression: str) -> Window | None:
        self.calls.append(("resolve_time", {"expression": expression}))
        return self._windows.get(expression.strip().lower())

    async def coverage(self, start: datetime, end: datetime) -> dict[str, object]:
        self.calls.append(("coverage", {"from": start, "to": end}))
        result = self._responses.get("coverage")
        return result if isinstance(result, dict) else coverage()

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object],
        start: datetime,
        end: datetime,
    ) -> dict[str, object]:
        self.windows.append((start, end))
        self.calls.append((name, dict(arguments)))
        if name in self._flaky_raises:
            # Once, so the loop's recovery is what the test observes rather than the
            # abort that a permanently dead service would reach instead.
            self._flaky_raises.discard(name)
            raise httpx.ConnectError(f"{name}: connection refused")
        if name in self._flaky:
            return {"error": True, "status": 503, "detail": self._flaky.pop(name)}
        if name in self._errors:
            return {"error": True, "status": 422, "detail": self._errors[name]}
        result = self._responses.get(name)
        if isinstance(result, dict):
            return result
        return {"error": True, "status": 404, "detail": f"{name} has no fixture"}

    async def part_exists(self, serial: str) -> bool:
        return serial in self._known


def coverage(
    gaps: list[dict[str, str]] | None = None, *, events: int = 4_211
) -> dict[str, object]:
    """The `/coverage` response, as the agent reads it."""
    return {
        "window": {"from_ts": "2026-09-12T13:30:00Z", "to_ts": "2026-09-12T14:30:00Z"},
        "gaps": gaps or [],
        "covered_fraction": 1.0 if not gaps else 0.9,
        "fully_covered": not gaps,
        "observed": {
            "from_ts": "2026-09-12T13:30:00Z" if events else None,
            "to_ts": "2026-09-12T14:30:00Z" if events else None,
            "events": events,
        },
    }


GAP = {
    "from_ts": "2026-09-12T13:40:00Z",
    "to_ts": "2026-09-12T13:45:00Z",
    "reason": "plant_unreachable",
}


def stats(
    total: int,
    rejects: int,
    gaps: list[dict[str, str]],
    *,
    rejects_without_class: int = 0,
) -> dict[str, object]:
    """The analysis service's stats response, as the agent reads it.

    `rejects_without_class` defaults to none, which is the ordinary window; pass a number
    to stand in for a window the breakdown cannot fully explain -- a row predating §3.4's
    vector, or §3.5 scenario 6's decay across every class.
    """
    return {
        "window": {"from_ts": "2026-09-12T13:30:00Z", "to_ts": "2026-09-12T14:30:00Z"},
        "total": total,
        "rejects": rejects,
        "by_defect_class": [{"defect_class": "gap", "count": rejects}]
        if rejects
        else [],
        "defect_class_threshold": 0.5,
        "rejects_without_class": rejects_without_class,
        "sample_serials": ["A-00000007"] if rejects else [],
        "coverage": coverage(gaps),
    }


def stop(
    identifier: str, seconds: float, category: str = "starvation"
) -> dict[str, object]:
    return {
        "id": identifier,
        "from_ts": "2026-09-12T13:30:00Z",
        "to_ts": "2026-09-12T13:40:00Z",
        "duration_seconds": seconds,
        "started_before_window": False,
        "open_at_window_end": False,
        "category": category,
    }


def stop_list(
    *entries: dict[str, object], gaps: list[dict[str, str]] | None = None
) -> dict[str, object]:
    return {
        "window": {"from_ts": "2026-09-12T13:30:00Z", "to_ts": "2026-09-12T14:30:00Z"},
        "coverage": coverage(gaps),
        "stops": list(entries),
        "micro_stops": 0,
        "micro_stop_threshold_seconds": 30.0,
        "truncated": False,
    }


def stop_detail(identifier: str, seconds: float) -> dict[str, object]:
    return {
        "stop": stop(identifier, seconds),
        "as_of": "2026-09-12T14:30:00Z",
        "coverage": coverage(),
        "history_from_ts": "2026-09-12T12:30:00Z",
        "timeline": [],
        "buffer_levels": [],
        "alarms": [],
        "derivation": {
            "links": [
                {
                    "station": "S1",
                    "state": "starved",
                    "from_ts": "2026-09-12T13:30:00Z",
                    "to_ts": None,
                    "reason": "feeder empty",
                    "buffer": None,
                    "buffer_condition_since": None,
                }
            ],
            "termination": "root_found",
            "category": "starvation",
            "cause_candidates": [],
            "unexplained": None,
        },
    }


def line_status(latest: str | None = "2026-09-12T14:29:00Z") -> dict[str, object]:
    return {
        "as_of": "2026-09-12T14:30:00Z",
        "latest_data_at": latest,
        "staleness_seconds": 60.0 if latest else None,
        "live": bool(latest),
        "live_within_seconds": 120.0,
        "stations": [
            {"station": "S1", "state": "running", "reason": None, "since": None}
        ],
        "buffers": [],
        "active_alarms": [],
        "last_part_out": None,
    }
