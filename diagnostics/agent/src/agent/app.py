"""POST /ask."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from agent.answer import Answer
from agent.pipeline import run

app = FastAPI(title="machine-agent agent", version="0.1.0")


class Question(BaseModel):
    question: str
    session_id: str = "anonymous"


@app.post("/ask", operation_id="ask")
async def ask(body: Question) -> Answer:
    return await run(body.question, body.session_id)
