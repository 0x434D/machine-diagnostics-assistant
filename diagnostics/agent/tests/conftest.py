"""Fixtures over the fakes in fakes.py.

A fake is right here and a container would be wrong: these tests are about the pipeline's
behaviour when the data says a particular thing, and the queries themselves are already
tested against real Postgres in the analysis package.
"""

from __future__ import annotations

import pytest

from .fakes import GAP, FakeAnalysis, coverage, stats


@pytest.fixture
def fake_analysis() -> FakeAnalysis:
    return FakeAnalysis({"inspection_stats": stats(total=600, rejects=30, gaps=[])})


@pytest.fixture
def fake_analysis_with_gap() -> FakeAnalysis:
    return FakeAnalysis(
        {
            "inspection_stats": stats(total=600, rejects=30, gaps=[GAP]),
            "coverage": coverage([GAP]),
        }
    )


@pytest.fixture
def fake_analysis_empty() -> FakeAnalysis:
    return FakeAnalysis({"inspection_stats": stats(total=0, rejects=0, gaps=[])})
