"""POST /ask, as server-sent events.

§7.2 asks for the reasoning to be visible. The steps arrive as they happen and the answer
object arrives last, so the reader watches the pipeline work rather than a spinner — and
what they watch is emitted by the code that does the work, not narrated beside it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.pipeline import Progress, stream

app = FastAPI(title="machine-agent agent", version="0.1.0")


class Question(BaseModel):
    question: str
    session_id: str = "anonymous"


def _event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"


async def _events(question: str, session_id: str) -> AsyncIterator[str]:
    async for item in stream(question, session_id):
        if isinstance(item, Progress):
            yield _event("progress", json.dumps({"message": item.message}))
        else:
            yield _event("answer", item.model_dump_json())


@app.post("/ask", operation_id="ask")
async def ask(body: Question) -> StreamingResponse:
    return StreamingResponse(
        _events(body.question, body.session_id),
        media_type="text/event-stream",
        # A proxy that buffers this has turned the stream back into a wait.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
