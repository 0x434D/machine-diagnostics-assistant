"""`POST /ask` streams progress and then the answer.

§7.2 asks for the reasoning to be visible rather than for a spinner: the user sees which
tool ran before the sentences arrive. These tests own the framing only — that the wire
format is server-sent events, that progress precedes the answer, and that exactly one
answer event closes the stream. What the answer *says* is the pipeline's tests.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from agent import app as app_module
from agent.answer import Answer, Method
from agent.pipeline import Progress
from fastapi.testclient import TestClient


def _events(body: str) -> list[tuple[str, str]]:
    """Parse an SSE body into (event, data) pairs."""
    out: list[tuple[str, str]] = []
    for block in body.strip().split("\n\n"):
        name = data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        out.append((name, data))
    return out


@pytest.fixture
def scripted_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = Answer(
        findings=[],
        answer_markdown="600 parts, 30 rejected.",
        method=Method(tools_called=["inspection_stats"], provider="scripted"),
    )

    async def fake_stream(
        question: str, session_id: str
    ) -> AsyncIterator[Progress | Answer]:
        del question, session_id
        yield Progress("reading inspection results")
        yield Progress("checking coverage")
        yield answer

    monkeypatch.setattr(app_module, "stream", fake_stream)


@pytest.mark.usefixtures("scripted_stream")
def test_progress_arrives_before_the_answer() -> None:
    with TestClient(app_module.app) as client:
        response = client.post("/ask", json={"question": "how many rejects?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _events(response.text)
    assert [name for name, _ in events] == ["progress", "progress", "answer"]
    assert json.loads(events[0][1])["message"] == "reading inspection results"


@pytest.mark.usefixtures("scripted_stream")
def test_the_answer_event_carries_the_whole_answer_object() -> None:
    """Not prose on the wire: §6.3's object, so the UI renders findings and citations
    rather than parsing sentences back apart."""
    with TestClient(app_module.app) as client:
        response = client.post("/ask", json={"question": "how many rejects?"})

    _, data = _events(response.text)[-1]
    parsed = Answer.model_validate_json(data)

    assert parsed.answer_markdown == "600 parts, 30 rejected."
    assert parsed.method.provider == "scripted"
