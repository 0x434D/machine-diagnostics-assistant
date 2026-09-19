"""POST /ask, as server-sent events; §7.2's trace and feedback; and what only an admin sees.

§7.2 asks for the reasoning to be visible. The steps arrive as they happen and the answer
object arrives last, so the reader watches the pipeline work rather than a spinner — and
what they watch is emitted by the code that does the work, not narrated beside it.

The stream's **first** event names the exchange, before any step has run. That is what gives
the trace and the feedback an address: a client that has the `session_id` and the `seq` can
open the trace for an answer that never arrived, and can come back to the two feedback
questions an hour later from a page it reloaded.

Since M5 every endpoint requires a token (§10.5), declared once for the whole application.
The session row that `/ask` writes carries the `sub` off that token, and everything here
that names a session checks it. §10.5 has no permission matrix and this is not one: it is a
single rule, applied in one direction — a session, its questions and the reasoning behind
their answers belong to whoever opened it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated

from auth.requests import admin, presented, principal
from auth.tokens import Principal
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import classify, pipeline
from agent.answer import Answer
from agent.config import Settings
from agent.pipeline import Progress, knowledge_base, stream
from agent.provider import MODEL
from agent.records import Feedback, Trace
from agent.sessions import (
    addressed,
    answered,
    asked,
    feedback_on,
    remember,
    subject_of,
    trace_of,
)

app = FastAPI(
    title="machine-agent agent",
    version="0.1.0",
    # §10.5, application-wide rather than per route: an endpoint added tomorrow is closed by
    # the line that closed the ones added today, and the endpoint nobody remembered to
    # decorate is the one §1.8 is about.
    dependencies=[Depends(principal)],
    # The three routes FastAPI would add for itself are not APIRoutes, so an
    # application-wide dependency does not reach them — they would be unauthenticated
    # endpoints describing every other one. `analysis.app` says the same in more words.
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


class Question(BaseModel):
    question: str

    session_id: uuid.UUID | None = None
    """The conversation this question continues, or `None` to open one.

    A uuid since M5, because it is `agent.sessions.id` and that column has been one since
    migration 0001. It used to be a free string defaulting to `"anonymous"`, which was
    honest while nothing wrote the table and is a lie the moment something does."""


class Exchange(BaseModel):
    """The `session` event, first on the stream: what this question and its answer are
    addressed by for the rest of their life.

    `seq` is the question's row in `agent.messages`. `agent.sessions` says why the answer's
    own row is not the address: this number is promised before the pipeline has taken a
    step, and a number promised that early can only name a row that already exists.
    """

    session_id: uuid.UUID
    seq: int


class KnowledgeReloaded(BaseModel):
    """What `POST /knowledge/reload` answers: how many documents the new index holds.

    A count rather than an acknowledgement, because the failure this action exists to make
    visible is a tree that loaded fewer files than the operator just added — and a bare 204
    would report a successful reload of the wrong directory exactly as happily.
    """

    documents: int


class Prompts(BaseModel):
    """§10.5's *"raw model exchange and system prompts"*, as much of it as exists.

    Every fixed instruction this service sends a model, so that an admin auditing what the
    agent tells the model reads it from the running service rather than from the source of
    whatever version they hope is deployed.

    **What is not here is the transcript of a run.** §7.2's trace of one answer — the SOPs
    loaded, every tool call with its arguments and timings, the budget spent — is served
    per message by `trace` below and is open to `user`, because it is the asker's own
    reasoning to inspect. What has no endpoint at all is the raw model exchange: the
    messages the provider was sent are held for the length of a run and stored nowhere, so
    there is nothing for this to serve.
    """

    provider: str
    model: str
    classification: str
    investigation: str
    out_of_scope: str


def now() -> datetime:
    """The injected clock (CLAUDE.md). UTC and timezone-aware, which `created_at` is."""
    return datetime.now(UTC)


def clock() -> Callable[[], datetime]:
    """The clock itself rather than one reading of it.

    `/ask` writes two rows seconds apart — the question when it arrives, the answer when
    the pipeline finishes — and a single injected `datetime` shared between them would
    record every answer as instantaneous.
    """
    return now


PrincipalDep = Annotated[Principal, Depends(principal)]
ClockDep = Annotated[Callable[[], datetime], Depends(clock)]


NOT_YOURS = "this session belongs to another user"
"""One sentence for the one rule, so reading a session and writing to it refuse alike."""


def _event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"


async def _events(
    question: str,
    session_id: uuid.UUID,
    seq: int,
    token: str | None,
    at: Callable[[], datetime],
) -> AsyncIterator[str]:
    answer: Answer | None = None
    trace: Trace | None = None

    yield _event("session", Exchange(session_id=session_id, seq=seq).model_dump_json())
    async for item in stream(question, token=token):
        if isinstance(item, Progress):
            yield _event("progress", json.dumps({"message": item.message}))
        elif isinstance(item, Answer):
            answer = item
        else:
            trace = item

    if answer is None or trace is None:
        # The pipeline yields exactly one of each and `stream` is the only producer, so
        # this is a broken contract rather than a condition to recover from.
        raise RuntimeError("the pipeline ended without an answer and a trace")

    # Stored before the answer event, not after: the client is told the trace's address
    # before the run starts, and a UI that opens it the moment the answer lands must not
    # race the write that puts it there.
    await answered(session_id, seq, answer.answer_markdown, trace, at(), Settings())
    yield _event("answer", answer.model_dump_json())


@app.post("/ask", operation_id="ask")
async def ask(
    body: Question, request: Request, who: PrincipalDep, at: ClockDep
) -> StreamingResponse:
    settings = Settings()
    if body.session_id is not None:
        # A session belongs to whoever opened it, and from M6 a question asked into one
        # leaves a message and a trace in it. Before M6 continuing somebody else's session
        # wrote nothing and cost nothing; now it would put a stranger's question into an
        # audit trail attributed to its owner, so the same rule the two endpoints below
        # apply to reading applies here to writing.
        opened_by = await subject_of(body.session_id, settings)
        if opened_by is not None and opened_by != who.subject:
            raise HTTPException(status_code=403, detail=NOT_YOURS)
    session = await remember(who.subject, at(), settings, body.session_id)
    # Both writes happen here rather than inside the stream: a question that cannot be
    # recorded must fail as a request, with a status code, instead of as a stream that
    # opened successfully and then stopped.
    seq = await asked(session, body.question, at(), settings)
    return StreamingResponse(
        # The caller's own token travels on to the analysis service, which refuses an
        # unauthenticated request like everything else since M5. `agent.tools` says why it
        # is forwarded rather than exchanged for a credential of the agent's own.
        _events(body.question, session, seq, presented(request), at),
        media_type="text/event-stream",
        # A proxy that buffers this has turned the stream back into a wait.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _owned(session_id: uuid.UUID, seq: int, who: Principal) -> None:
    """Refuse a message that does not exist, or one that is not this caller's.

    §10.5 is explicit that there are no per-resource permission rules and this is not one:
    there is a single fact being checked, that the session carries the `sub` presented. What
    it stops is the only thing an unguessable id does not — a token that is valid for this
    deployment reading the reasoning behind somebody else's question.

    §14 keeps the two refusals apart. 404 is *no such thing*; 403 is *it is not yours*, and
    it says so plainly rather than pretending the session is absent, because a session id is
    unguessable by construction (§6.10) and there is nothing left to conceal from a caller
    who already has one.
    """
    message = await addressed(session_id, seq, Settings())
    if message is None:
        raise HTTPException(status_code=404, detail=f"no session {session_id}")
    if message.subject != who.subject:
        raise HTTPException(status_code=403, detail=NOT_YOURS)
    if not message.holds_message:
        raise HTTPException(
            status_code=404, detail=f"session {session_id} holds no message {seq}"
        )


@app.get(
    "/sessions/{session_id}/messages/{seq}/trace",
    operation_id="messageTrace",
)
async def trace(session_id: uuid.UUID, seq: int, who: PrincipalDep) -> Trace:
    """§7.2's reasoning trace for one answered message.

    Open to `user` rather than to `admin`: §10.5's admin column is the raw model exchange,
    and this is the asker's own reasoning — the thing §7.2 puts under every answer so that
    "did it follow the method" is checkable by the person reading it.
    """
    await _owned(session_id, seq, who)
    recorded = await trace_of(session_id, seq, Settings())
    if recorded is None:
        # A message whose run never finished. Deliberate, and distinct from the 404 above:
        # the exchange exists and is the caller's, and there is no trace to give for it.
        raise HTTPException(
            status_code=404, detail=f"message {seq} has no recorded trace"
        )
    return recorded


@app.post(
    "/sessions/{session_id}/messages/{seq}/feedback",
    operation_id="giveFeedback",
)
async def feedback(
    session_id: uuid.UUID, seq: int, given: Feedback, who: PrincipalDep, at: ClockDep
) -> Feedback:
    """§7.2's two questions, answerable one at a time and in either order.

    Returns everything now stored for the message, not just what this request carried, so a
    client that answered the second question can render both without asking again.
    """
    await _owned(session_id, seq, who)
    return await feedback_on(session_id, seq, given, at(), Settings())


@app.get("/prompts", operation_id="prompts", dependencies=[Depends(admin)])
def prompts() -> Prompts:
    """§10.5's matrix: the second of its four admin rows that exists to be gated in M5."""
    settings = Settings()
    return Prompts(
        provider=settings.provider,
        model=MODEL,
        classification=classify.SYSTEM,
        investigation=pipeline.SYSTEM,
        out_of_scope=pipeline.DECLINE,
    )


@app.post(
    "/knowledge/reload", operation_id="reloadKnowledge", dependencies=[Depends(admin)]
)
def reload_knowledge() -> KnowledgeReloaded:
    """§10.5's matrix: reloading the knowledge base, the other of its two live admin rows.

    Routing already re-reads what *changed* on every question (§6.2 hot-reloads on a
    fingerprint of the tree). This forces the re-read the fingerprint would not ask for — an
    edit made within the same second as the last one, a bind mount whose mtimes did not
    survive the copy — and it is the action an operator takes when a document they just
    wrote is not being cited.

    **It reloads this service's index and no other.** §6.2's loader has three consumers and
    none depends on either of the others, so the analysis service and the MCP server hold
    their own; there is no shared cache to invalidate because there is no shared cache. This
    endpoint is here rather than on the analysis service because §6.11 generates the MCP
    tool set from that service's contract and refuses anything that is not a GET — a write
    endpoint there would break the binding at load.
    """
    index = knowledge_base(Settings().knowledge_root).reload()
    return KnowledgeReloaded(documents=len(index.by_id))
