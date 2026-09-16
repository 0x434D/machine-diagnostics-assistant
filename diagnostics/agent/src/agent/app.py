"""POST /ask, as server-sent events, and the one thing §10.5 lets only an admin see.

§7.2 asks for the reasoning to be visible. The steps arrive as they happen and the answer
object arrives last, so the reader watches the pipeline work rather than a spinner — and
what they watch is emitted by the code that does the work, not narrated beside it.

Since M5 both endpoints require a token (§10.5). The session row that `/ask` writes carries
the `sub` off that token, which is what makes §5.2's trace tables an audit trail rather than
a log of questions nobody can attribute.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from auth.requests import admin, presented, principal
from auth.tokens import Principal
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import classify, pipeline
from agent.config import Settings
from agent.pipeline import Progress, knowledge_base, stream
from agent.provider import MODEL
from agent.sessions import remember

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

    **What is not here is the transcript of a run.** `agent.traces` is written by nothing
    until M6, so there is no recorded exchange to serve; the request-scoped half of §10.5's
    row arrives with the table. What §7.2's trace already shows — the tools called, the
    documents routed — travels in the answer object and is open to `user`.
    """

    provider: str
    model: str
    classification: str
    investigation: str
    out_of_scope: str


def now() -> datetime:
    """The injected clock (CLAUDE.md). UTC and timezone-aware, which `created_at` is."""
    return datetime.now(UTC)


PrincipalDep = Annotated[Principal, Depends(principal)]
NowDep = Annotated[datetime, Depends(now)]


def _event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"


async def _events(
    question: str, session_id: str, token: str | None
) -> AsyncIterator[str]:
    async for item in stream(question, session_id, token=token):
        if isinstance(item, Progress):
            yield _event("progress", json.dumps({"message": item.message}))
        else:
            yield _event("answer", item.model_dump_json())


@app.post("/ask", operation_id="ask")
async def ask(
    body: Question, request: Request, who: PrincipalDep, at: NowDep
) -> StreamingResponse:
    session = await remember(who.subject, at, Settings(), body.session_id)
    return StreamingResponse(
        # The caller's own token travels on to the analysis service, which refuses an
        # unauthenticated request like everything else since M5. `agent.tools` says why it
        # is forwarded rather than exchanged for a credential of the agent's own.
        _events(body.question, str(session), presented(request)),
        media_type="text/event-stream",
        # A proxy that buffers this has turned the stream back into a wait.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
