"""Fixtures over the fakes in fakes.py.

A fake is right here and a container would be wrong: these tests are about the pipeline's
behaviour when the data says a particular thing, and the queries themselves are already
tested against real Postgres in the analysis package.
"""

from __future__ import annotations

import pytest

from .fakes import FakeAnalysis, stats


@pytest.fixture
def fake_analysis() -> FakeAnalysis:
    return FakeAnalysis(stats(total=600, rejects=30, gaps=[]))


@pytest.fixture
def fake_analysis_with_gap() -> FakeAnalysis:
    return FakeAnalysis(
        stats(
            total=600,
            rejects=30,
            gaps=[
                {
                    "from_ts": "2026-09-12T13:40:00Z",
                    "to_ts": "2026-09-12T13:45:00Z",
                    "reason": "plant_unreachable",
                }
            ],
        )
    )


@pytest.fixture
def fake_analysis_empty() -> FakeAnalysis:
    return FakeAnalysis(stats(total=0, rejects=0, gaps=[]))
