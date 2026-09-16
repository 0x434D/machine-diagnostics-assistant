"""Fixtures over the fakes in fakes.py.

A fake is right here and a container would be wrong: these tests are about the pipeline's
behaviour when the data says a particular thing, and the queries themselves are already
tested against real Postgres in the analysis package.
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
from auth.testing import AUDIENCE, ISSUER, PUBLIC_PEM, mint

from .fakes import GAP, FakeAnalysis, coverage, stats


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


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    """`POST /ask` records the session and its subject (§5.2), which needs Postgres.

    These tests are about the endpoint's framing, and `test_agent_schema.py` already proves
    the write against a real database — where it belongs, because a session row asserted
    against a fake is a statement about the fake. So the write is replaced here and its
    absence is not silent: the id it returns is the one the assertions read back.
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

    monkeypatch.setattr(app_module, "remember", recorded)
    return session


@pytest.fixture
def scripted_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pipeline replaced by two progress lines and an answer.

    What the answer *says* is the pipeline's own tests; these are about the wire.
    """
    answer = Answer(
        findings=[],
        answer_markdown="600 parts, 30 rejected.",
        method=Method(tools_called=["inspection_stats"], provider="scripted"),
    )

    async def fake_stream(
        question: str, session_id: str, *, token: str | None = None
    ) -> AsyncIterator[Progress | Answer]:
        del question, session_id, token
        yield Progress("reading inspection results")
        yield Progress("checking coverage")
        yield answer

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
