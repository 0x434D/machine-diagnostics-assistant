"""§7.2's trace and feedback, end to end, against the database that holds them.

Real Postgres and a real pipeline run, with only the analysis service faked. That split is
deliberate: what these tests claim is that the tool calls a run made survive a round trip
through `agent.traces` and come back addressable by the id the stream announced — and a
round trip through a column type, through jsonb, through a foreign key, is precisely the
claim a fake database cannot make on behalf of a real one. What the analysis service
answers is not part of the claim, and `fakes.py` says why it is a fake everywhere else in
this package.

Every assertion goes through the HTTP surface with a token on it, because the ownership
rule being tested is a property of the endpoint and the `sub` it reads, not of the SQL
underneath.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from agent import app as app_module
from agent import pipeline
from agent.answer import Answer
from agent.pipeline import Progress
from agent.providers_scripted import ScriptedProvider
from agent.records import Trace
from auth.testing import mint
from fastapi.testclient import TestClient

from .fakes import FakeAnalysis, as_client, stats

QUESTION = "how many rejects in the last hour?"

ASKER = "operator-7"
"""`auth.testing.mint`'s default subject, and therefore the `bearer` fixture's."""

BESIDE_THE_LOOP = frozenset({"resolve_time", "coverage"})
"""Calls the pipeline makes around the tool loop rather than inside it.

Stage 2 resolves the window and stage 3 checks coverage before the model is given a tool at
all, and §6.5's verification calls the fake back under its own `resolve:` and `fetch:`
prefixes. None of them is a step the model chose, which is what §7.2's trace records.
"""


@pytest.fixture
def analysis() -> FakeAnalysis:
    return FakeAnalysis({"inspection_stats": stats(total=600, rejects=30, gaps=[])})


@pytest.fixture
def client(
    agent_url: str, analysis: FakeAnalysis, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The service against the migrated database, answering with the real pipeline.

    The pipeline is reached through `app.stream` with the analysis client substituted, so
    the trace under test is the one §6.1's tool loop produced rather than one a fixture
    wrote — which is the whole of what "a real /ask produced" means here.
    """
    monkeypatch.setenv("AGENT_DATABASE_URL", agent_url)

    async def piped(
        question: str, *, token: str | None = None
    ) -> AsyncIterator[Progress | Answer | Trace]:
        async for item in pipeline.stream(
            question,
            analysis=as_client(analysis),
            provider=ScriptedProvider(),
            token=token,
        ):
            yield item

    monkeypatch.setattr(app_module, "stream", piped)
    with TestClient(
        app_module.app, headers={"Authorization": f"Bearer {mint(subject=ASKER)}"}
    ) as opened:
        yield opened


def events(body: str) -> list[tuple[str, str]]:
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


def ask(client: TestClient, question: str = QUESTION) -> list[tuple[str, str]]:
    response = client.post("/ask", json={"question": question})
    assert response.status_code == 200
    return events(response.text)


def named(stream: list[tuple[str, str]]) -> tuple[uuid.UUID, int]:
    """The session event, which is the first thing on the wire."""
    name, data = stream[0]
    assert name == "session", f"the stream opened with {name!r}"
    exchange = json.loads(data)
    return uuid.UUID(exchange["session_id"]), int(exchange["seq"])


def message(session_id: uuid.UUID, seq: int) -> str:
    return f"/sessions/{session_id}/messages/{seq}"


def test_the_id_the_stream_announces_is_the_one_the_trace_answers_to(
    client: TestClient,
) -> None:
    """The first event, before a single progress line. §7.2's trace and feedback have no
    address without it, and a client that only learned the id at the end could not open the
    trace of a run that died in the middle."""
    stream = ask(client)
    session_id, seq = named(stream)

    assert {name for name, _ in stream[1:-1]} == {"progress"}
    assert stream[-1][0] == "answer"
    assert client.get(f"{message(session_id, seq)}/trace").status_code == 200


def test_the_trace_holds_the_tool_calls_and_timings_the_run_made(
    client: TestClient, analysis: FakeAnalysis
) -> None:
    """§7.2 names what is stored: the SOPs loaded, every tool call with its arguments and
    timings, the budget consumed. Held against what the analysis client was *actually*
    asked, so this fails if the trace is assembled from the answer rather than recorded by
    the loop that made the calls."""
    stream = ask(client)
    session_id, seq = named(stream)
    answer = Answer.model_validate_json(stream[-1][1])

    stored = Trace.model_validate_json(
        client.get(f"{message(session_id, seq)}/trace").content
    )

    asked_for = [
        (name, arguments)
        for name, arguments in analysis.calls
        if ":" not in name and name not in BESIDE_THE_LOOP
    ]
    assert [(call.name, call.arguments) for call in stored.tool_calls] == asked_for
    assert stored.sops_loaded == answer.method.sops_used
    assert stored.budget.tool_turns == answer.method.budget_used
    assert all(call.duration_ms > 0 for call in stored.tool_calls)
    assert stored.timings.total_ms > 0


def test_answering_one_question_and_then_the_other_leaves_both_answered(
    client: TestClient,
) -> None:
    """§7.2's two questions are independent, which is why `agent.feedback` is nullable in
    both columns. An operator says the answer was useful, goes to the machine, and comes
    back an hour later to say whether it matched — and the first answer is still there."""
    session_id, seq = named(ask(client))
    where = f"{message(session_id, seq)}/feedback"

    first = client.post(where, json={"useful": True})
    second = client.post(where, json={"matched_reality": False, "comment": "carrier 7"})

    assert first.json() == {"useful": True, "matched_reality": None, "comment": None}
    assert second.json() == {
        "useful": True,
        "matched_reality": False,
        "comment": "carrier 7",
    }


def test_the_second_question_can_be_answered_first(client: TestClient) -> None:
    """The order is the operator's. §7.2 calls the second question the more valuable signal,
    and a client that had to answer the first one to reach it would be inventing an answer
    to the first."""
    session_id, seq = named(ask(client))
    where = f"{message(session_id, seq)}/feedback"

    client.post(where, json={"matched_reality": True})
    both = client.post(where, json={"useful": False})

    assert both.json() == {"useful": False, "matched_reality": True, "comment": None}


def test_another_users_trace_is_not_readable(client: TestClient) -> None:
    """§10.5 has no permission matrix and this is not one: the session carries the `sub` of
    whoever opened it, and the reasoning behind their question is theirs. 403 rather than
    404 (§14) — the caller is known, and the session id they already hold is unguessable by
    construction, so there is nothing left to conceal by pretending it does not exist."""
    session_id, seq = named(ask(client))
    stranger = {"Authorization": f"Bearer {mint(subject='someone-else')}"}

    refused = client.get(f"{message(session_id, seq)}/trace", headers=stranger)

    assert refused.status_code == 403
    assert "another user" in refused.json()["detail"]


def test_another_user_cannot_give_feedback_on_a_session_that_is_not_theirs(
    client: TestClient,
) -> None:
    """The half that would be worse to leave open. A trace read by a stranger is a leak; a
    stranger's feedback on somebody else's answer is a corrupted measurement, and M7 scores
    against this table."""
    session_id, seq = named(ask(client))
    stranger = {"Authorization": f"Bearer {mint(subject='someone-else')}"}

    refused = client.post(
        f"{message(session_id, seq)}/feedback", json={"useful": True}, headers=stranger
    )

    assert refused.status_code == 403


def test_another_user_cannot_ask_into_a_session_that_is_not_theirs(
    client: TestClient,
) -> None:
    """The write side of the same rule. Continuing somebody else's session cost nothing
    while nothing was stored; it now puts a stranger's question, and the trace of the answer
    to it, into an audit trail that says the session is its owner's."""
    session_id, _ = named(ask(client))
    stranger = {"Authorization": f"Bearer {mint(subject='someone-else')}"}

    refused = client.post(
        "/ask",
        json={"question": QUESTION, "session_id": str(session_id)},
        headers=stranger,
    )

    assert refused.status_code == 403


def test_a_session_nobody_opened_is_not_found(client: TestClient) -> None:
    assert client.get(f"{message(uuid.uuid4(), 1)}/trace").status_code == 404


def test_a_message_the_session_does_not_hold_is_not_found(client: TestClient) -> None:
    """Distinct from the session being absent, and from the caller being a stranger: §14
    keeps *no such thing* and *not yours* apart, and a seq off the end of a real session is
    the first of those."""
    session_id, seq = named(ask(client))

    assert client.get(f"{message(session_id, seq + 99)}/trace").status_code == 404


def test_a_second_question_in_one_session_gets_its_own_trace(
    client: TestClient,
) -> None:
    """The seq is what separates two answers in one conversation. Without this, the trace
    of the second question would overwrite the first or fail on its primary key, and §7.2's
    panel under the older answer would be showing the newer one's reasoning."""
    first_id, first_seq = named(ask(client))
    second = client.post(
        "/ask", json={"question": QUESTION, "session_id": str(first_id)}
    )
    second_id, second_seq = named(events(second.text))

    assert second_id == first_id
    assert second_seq != first_seq
    assert client.get(f"{message(first_id, first_seq)}/trace").status_code == 200
    assert client.get(f"{message(second_id, second_seq)}/trace").status_code == 200
