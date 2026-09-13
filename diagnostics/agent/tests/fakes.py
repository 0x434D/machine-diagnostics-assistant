"""The fake analysis service the agent tests run against.

A fake is right here and a container would be wrong: these tests are about the pipeline's
behaviour when the data says a particular thing, and the queries themselves are already
tested against real Postgres in the analysis package.
"""

from __future__ import annotations

from datetime import datetime


class FakeAnalysis:
    """Stands in for AnalysisClient. Knows one part, so a citation either resolves or does
    not, which is the distinction §6.5 depends on."""

    def __init__(self, stats: dict[str, object], known: set[str] | None = None) -> None:
        self._stats = stats
        self._known = known if known is not None else {"A-00000007"}
        self.calls = 0

    async def inspection_stats(
        self, start: datetime, end: datetime
    ) -> dict[str, object]:
        del start, end
        self.calls += 1
        return self._stats

    async def part_exists(self, serial: str) -> bool:
        return serial in self._known


def stats(total: int, rejects: int, gaps: list[dict[str, str]]) -> dict[str, object]:
    return {
        "window": {"from_ts": "2026-09-12T13:30:00Z", "to_ts": "2026-09-12T14:30:00Z"},
        "total": total,
        "rejects": rejects,
        "by_defect_class": [{"defect_class": "gap", "count": rejects}]
        if rejects
        else [],
        "sample_serials": ["A-00000007"] if rejects else [],
        "coverage": {"gaps": gaps},
    }
