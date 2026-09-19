"""Fixtures over the fakes in fakes.py, and the migrated database from database.py.

A fake is right for most of this package and a container would be wrong: these tests are
about the pipeline's behaviour when the data says a particular thing, and the queries
themselves are already tested against real Postgres in the analysis package.

The two suites that are about the agent's *own* schema are the exception, and they get the
real thing — a grant boundary or a jsonb round trip asserted against a fake is a statement
about the fake.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime

import pytest
from agent import app as app_module
from agent.answer import Answer, Method
from agent.config import Settings
from agent.pipeline import Progress
from agent.records import Budget, Timings, ToolCallRecord, Trace
from auth.testing import AUDIENCE, ISSUER, PUBLIC_PEM, mint

from .database import agent_url, owner_url, postgres_container
from .fakes import GAP, FakeAnalysis, coverage, stats

__all__ = ["agent_url", "owner_url", "postgres_container"]
"""Re-exported so pytest finds them: a fixture is only visible from a conftest, and two
suites need the same migrated database. `database.py` says why it is not in either of
them."""


@pytest.fixture(scope="session", autouse=True)
def authentication() -> Iterator[None]:
    """The service configured the way it is deployed: a public key, an audience, an issuer.

    Through the environment rather than through `dependency_overrides`, deliberately. A
    fixture that overrode the guard would leave every test in this package running against a
    service whose door the test suite propped open, and the one thing M5 must not do is
    prove the guard against a stand-in for it.
    """
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AUTH_PUBLIC_KEY", PUBLIC_PEM)
        environment.setenv("AUTH_AUDIENCE", AUDIENCE)
        environment.setenv("AUTH_ISSUER", ISSUER)
        yield


@pytest.fixture(scope="session")
def bearer() -> dict[str, str]:
    """A `user` token: §10.5 puts asking a question in both columns of its matrix."""
    return {"Authorization": f"Bearer {mint(role='user')}"}


SEQ = 1
"""The `seq` `no_database` hands back, and what `/ask` then names on the wire."""


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    """`POST /ask` records the session, the question and the trace (§5.2), which needs
    Postgres.

    These tests are about the endpoint's framing, and `test_agent_schema.py` and
    `test_trace_and_feedback.py` already prove the writes against a real database — where
    they belong, because a row asserted against a fake is a statement about the fake. So the
    writes are replaced here and their absence is not silent: the id and the seq they return
    are the ones the assertions read back.
    """
    session = uuid.uuid4()

    async def recorded(
        subject: str,
        now: datetime,
        settings: Settings,
        session_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        del subject, now, settings
        return session_id or session

    async def question_stored(
        session_id: uuid.UUID, question: str, now: datetime, settings: Settings
    ) -> int:
        del session_id, question, now, settings
        return SEQ

    async def answer_stored(
        session_id: uuid.UUID,
        seq: int,
        answer: str,
        trace: Trace,
        now: datetime,
        settings: Settings,
    ) -> None:
        del session_id, seq, answer, trace, now, settings

    monkeypatch.setattr(app_module, "remember", recorded)
    monkeypatch.setattr(app_module, "asked", question_stored)
    monkeypatch.setattr(app_module, "answered", answer_stored)
    return session


@pytest.fixture
def scripted_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline replaced by two progress lines, an answer and a trace.

    What the answer *says* is the pipeline's own tests; these are about the wire.
    """
    answer = Answer(
        findings=[],
        answer_markdown="600 parts, 30 rejected.",
        method=Method(tools_called=["inspection_stats"], provider="scripted"),
    )
    trace = Trace(
        sops_loaded=["CORE-01"],
        tool_calls=[
            ToolCallRecord(
                name="inspection_stats", arguments={}, duration_ms=1.0, failed=False
            )
        ],
        budget=Budget(tool_turns=1, tool_turns_limit=6),
        timings=Timings(total_ms=2.0, model_ms=1.0, tools_ms=1.0),
    )

    async def fake_stream(
        question: str, *, token: str | None = None
    ) -> AsyncIterator[Progress | Answer | Trace]:
        del question, token
        yield Progress("reading inspection results")
        yield Progress("checking coverage")
        yield answer
        yield trace

    monkeypatch.setattr(app_module, "stream", fake_stream)


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
