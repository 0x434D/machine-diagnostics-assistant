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
from knowledge.documents import DEFAULT_ROOT, load

DOCUMENTS: frozenset[str] = frozenset(load(DEFAULT_ROOT).by_id)
"""Every id the real tree defines. The analysis service serves `GET /knowledge/{id}` from
this same tree, so a fake that answered `True` to any id would let a cited procedure that
does not exist pass the check §6.5 asks for."""

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
        components: set[str] | None = None,
        lots: set[str] | None = None,
        stations: set[str] | None = None,
        documents: frozenset[str] = DOCUMENTS,
    ) -> None:
        self._responses = dict(responses or {})
        self._known = known if known is not None else {"A-00000007"}
        self._windows = dict(windows if windows is not None else WINDOWS)
        self._errors = dict(errors or {})
        self._flaky = dict(flaky or {})
        self._flaky_raises = set(flaky_raises or ())
        self._components = components if components is not None else {"C-1"}
        self._lots = lots if lots is not None else {"L-4471"}
        self._stations = stations if stations is not None else {"S1", "S2", "S3", "S4"}
        self._documents = documents
        self._stops = _stop_ids(self._responses)
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

    async def exists(
        self, name: str, arguments: Mapping[str, object], window: Window
    ) -> bool:
        """§6.5's resolution, per kind. The fake knows what it was given and nothing else,
        so a citation to something no fixture holds does not resolve."""
        del window
        self.calls.append((f"resolve:{name}", dict(arguments)))
        if name == "get_part":
            return str(arguments.get("serial")) in self._known
        if name == "component_assembly":
            return str(arguments.get("serial")) in self._components
        if name == "get_stop":
            return str(arguments.get("identifier")) in self._stops
        if name == "lot_parts":
            return str(arguments.get("lot_code")) in self._lots
        if name == "signal_trend":
            return str(arguments.get("station")) in self._stations
        raise AssertionError(f"the fake was not asked to resolve {name}")

    async def fetch(
        self, name: str, arguments: Mapping[str, object], window: Window
    ) -> dict[str, object]:
        del window
        self.calls.append((f"fetch:{name}", dict(arguments)))
        result = self._responses.get(name)
        if isinstance(result, dict):
            return result
        # What the real client does when a window-scoped report does not arrive: a report
        # that failed is not evidence of absence, so it is a fault rather than a "no".
        raise RuntimeError(f"resolving {name} failed: no fixture")

    async def knowledge_exists(self, document_id: str) -> bool:
        self.calls.append(("knowledge_exists", {"id": document_id}))
        return document_id in self._documents


def _stop_ids(responses: Mapping[str, object]) -> set[str]:
    """Every stop id the fixtures hand out, so `get_stop` resolves exactly those."""
    ids: set[str] = set()
    listed = responses.get("list_stops")
    if isinstance(listed, dict):
        entries = listed.get("stops")
        if isinstance(entries, list):
            ids.update(
                str(entry["id"])
                for entry in entries
                if isinstance(entry, dict) and entry.get("id")
            )
    detail = responses.get("get_stop")
    if isinstance(detail, dict):
        one = detail.get("stop")
        if isinstance(one, dict) and one.get("id"):
            ids.add(str(one["id"]))
    return ids


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


def stop_detail_intervention(identifier: str = "s-1") -> dict[str, object]:
    """DP-11's shape, in the data §5.4 returns.

    The chain terminates at S3 in `held`; S3 carries no alarm of its own; and S1 was
    already starved three minutes before S3 was held. The computation cannot separate
    "held because it faulted" from "held because somebody stopped it" — that is the whole
    reason `Held` is a cause candidate — so the ordering is the only thing that can.
    """
    detail = stop_detail(identifier, 660.0)
    detail["alarms"] = [
        {
            "id": 41,
            "station": "S1",
            "code": "A-101",
            "text": "Feeder lane 1 empty",
            "severity": 2,
            "raised_at": "2026-09-12T13:31:00Z",
            "acked_at": None,
            "cleared_at": None,
            "status": "active",
            "active": True,
        }
    ]
    detail["timeline"] = [
        {
            "station": "S1",
            "state": "starved",
            "from_ts": "2026-09-12T13:31:00Z",
            "to_ts": None,
            "reason": "feeder empty",
            "reason_buffer": None,
        },
        {
            "station": "S3",
            "state": "held",
            "from_ts": "2026-09-12T13:34:00Z",
            "to_ts": None,
            "reason": None,
            "reason_buffer": None,
        },
    ]
    detail["derivation"] = {
        "links": [
            {
                "station": "S3",
                "state": "held",
                "from_ts": "2026-09-12T13:34:00Z",
                "to_ts": None,
                "reason": None,
                "buffer": None,
                "buffer_condition_since": None,
            }
        ],
        "termination": "root_found",
        "category": "fault",
        "cause_candidates": [],
        "unexplained": None,
    }
    return detail


def alarm_list(*identifiers: int) -> dict[str, object]:
    return {
        "window": {"from_ts": "2026-09-12T13:30:00Z", "to_ts": "2026-09-12T14:30:00Z"},
        "station": None,
        "alarms": [
            {
                "id": identifier,
                "station": "S2",
                "code": "A-207",
                "text": "Joining force out of tolerance",
                "severity": 2,
                "raised_at": "2026-09-12T13:31:00Z",
                "acked_at": None,
                "cleared_at": None,
                "status": "active",
                "active": True,
            }
            for identifier in identifiers
        ],
    }


def pattern_report(dimension: str = "carrier", *keys: str) -> dict[str, object]:
    return {
        "window": {"from_ts": "2026-09-12T13:30:00Z", "to_ts": "2026-09-12T14:30:00Z"},
        "coverage": coverage(),
        "alpha": 0.05,
        "minimum_sample": 30,
        "correction": "benjamini_hochberg",
        "defect_class_threshold": 0.5,
        "dimensions": [
            {
                "dimension": dimension,
                "within": None,
                "comparable": True,
                "not_comparable": None,
                "unattributed": 0,
                "patterns": [
                    {
                        "value": key,
                        "stratum": None,
                        "observed": 42,
                        "trials": 214,
                        "observed_share": 0.2,
                        "expected_share": 0.05,
                        "effect_size": 0.15,
                        "p_value": 0.001,
                        "adjusted_p_value": 0.003,
                        "verdict": "significant",
                    }
                    for key in keys
                ],
            }
        ],
        "significant_count": len(keys),
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
