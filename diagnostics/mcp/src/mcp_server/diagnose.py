"""`diagnose(question)` — §6.11's third primitive: the full staged pipeline.

The pipeline is the agent's, and this calls it rather than reimplementing it. It calls it
over the agent service's own `POST /ask` — the same endpoint the diagnostics UI uses — for a
reason worth stating, because importing `agent.pipeline` in-process was the obvious
alternative:

**There must be exactly one agent.** The pipeline writes §5.2's sessions, messages and traces
and holds the provider, the retrieval budget and the knowledge root. A second process running
the same code with its own configuration would be a second agent answering the same questions
with a different budget and no trace, and nothing in either would say so. Going through the
service means an answer reached over MCP and the same answer reached through the UI came out
of one process, under one configuration, into one audit trail — which is exactly the claim
§6.11 makes and Task 8 has to prove.

The answer object crosses unchanged. §6.3's whole point is that nothing is parsed out of
prose, and re-summarising it here for a "nicer" tool result would be this file doing the one
thing the answer object exists to prevent.
"""

from __future__ import annotations

import json
from typing import cast

import httpx
import mcp_types as types

DIAGNOSE_TOOL = "diagnose"
"""The one tool in this server that is not generated from the contract.

§6.11 lists it as its own primitive, beside `tools` and `resources`, because it is not an
analysis query: it is the staged pipeline with verified citations. `tests/test_parity.py`
asserts it is the *only* such tool, so a hand-written capability added beside the generated
ones fails the gate rather than joining them.
"""

_ANSWER_EVENT = "answer"


def tool() -> types.Tool:
    return types.Tool(
        name=DIAGNOSE_TOOL,
        description=(
            "Ask the diagnostics agent a question in plain language and get its answer "
            "object: findings with an epistemic basis (measured, derived or hypothesis), "
            "typed citations that have each been resolved against the database, the method "
            "it followed, its caveats, and a contradiction when it disagrees with the "
            "computed propagation. It classifies the question, resolves the time window in "
            "code, checks data coverage before answering, loads the relevant procedures "
            "from the knowledge base and calls the same read-only analysis tools this "
            "server exposes. It reads the plant's history and cannot act on the plant."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question, in plain language.",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Conversation this question belongs to. Omit for a one-off "
                        "question; pass the same value to continue one."
                    ),
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        annotations=types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )


class AgentClient:
    """The agent service's `POST /ask`, as one call that returns the answer object."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client

    async def diagnose(
        self, question: str, session_id: str | None = None, token: str | None = None
    ) -> types.CallToolResult:
        answer = await self._ask(question, session_id, token)
        markdown = answer.get("answer_markdown")
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=markdown
                    if isinstance(markdown, str)
                    else json.dumps(answer, indent=2),
                )
            ],
            structured_content=answer,
        )

    async def _ask(
        self, question: str, session_id: str | None, token: str | None
    ) -> dict[str, object]:
        """The answer event of the agent's SSE stream, as the object §6.3 defines.

        Raises if the stream ends without one. An empty answer is not a possible outcome of
        the pipeline — it either answers, answers partially and says so, or fails — so a
        stream that produced none means something broke, and reporting "no findings" would
        be the quiet wrong answer this whole system exists to not give.
        """
        body: dict[str, object] = {"question": question}
        if session_id is not None:
            body["session_id"] = session_id

        # The caller's token, forwarded: `POST /ask` refuses an unauthenticated request
        # since M5, and the answer this returns is the asker's, recorded against the `sub`
        # on their own token in §5.2's `sessions`.
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if self._client is not None:
            return await self._stream(self._client, body, headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._stream(client, body, headers)

    async def _stream(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
        headers: dict[str, str],
    ) -> dict[str, object]:
        event = ""
        async with client.stream(
            "POST", f"{self._base_url}/ask", json=body, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:") and event == _ANSWER_EVENT:
                    # Progress events are §7.2's visible reasoning and belong to a stream a
                    # human watches; what the answer was reached by is in the answer's own
                    # `method`, which is what a calling agent needs and already has.
                    return _object(json.loads(line.removeprefix("data:").strip()))
        raise RuntimeError("the agent's stream ended without an answer")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"the agent's answer is {type(value).__name__}, not an object")
    return cast(dict[str, object], value)
