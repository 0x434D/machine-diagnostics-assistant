"""`diagnose(question)` — the staged pipeline, reached rather than reimplemented.

The agent service is faked here because what this asserts is the binding: that the answer
object crosses unchanged, and that a stream which produced no answer is a failure rather
than an empty one. The pipeline's own behaviour is tested in the agent package, against the
same code this calls.
"""

from __future__ import annotations

import json

import httpx
import mcp_types as types
import pytest
from mcp_server.config import Settings
from mcp_server.diagnose import DIAGNOSE_TOOL, AgentClient
from mcp_server.diagnose import tool as diagnose_tool

ANSWER = {
    "findings": [
        {
            "statement": "S3 starved at 02:14 because S2 stopped and B2_3 drained.",
            "basis": "derived",
            "citations": [{"kind": "stop", "id": "12"}],
        }
    ],
    "answer_markdown": "S3 starved at 02:14 because S2 stopped and B2_3 drained.",
    "method": {
        "sops_used": ["SOP-01"],
        "tools_called": ["listStops"],
        "budget_used": 1,
    },
    "caveats": ["This answer was produced by a scripted provider, not a model."],
}


def agent_over(settings: Settings, body: str) -> AgentClient:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )

    return AgentClient(
        "http://agent.test",
        settings.diagnose_timeout_seconds,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def stream(*events: tuple[str, object]) -> str:
    return "".join(
        f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
    )


async def test_the_answer_object_crosses_unchanged(settings: Settings) -> None:
    """§6.3's point is that nothing is parsed out of prose. Re-summarising the answer here
    for a tidier tool result would be this file doing the one thing that object prevents."""
    result = await agent_over(
        settings,
        stream(("progress", {"message": "reading stop events"}), ("answer", ANSWER)),
    ).diagnose("why did S3 stop?")

    assert result.structured_content == ANSWER


async def test_the_text_block_is_the_answers_own_markdown(settings: Settings) -> None:
    """A client that shows only the content blocks sees the answer, not a JSON dump."""
    result = await agent_over(settings, stream(("answer", ANSWER))).diagnose("why?")

    block = result.content[0]
    assert isinstance(block, types.TextContent)
    assert block.text == ANSWER["answer_markdown"]


async def test_a_stream_without_an_answer_is_a_failure(settings: Settings) -> None:
    """Not an empty answer.

    The pipeline either answers, answers partially and says so, or fails. A stream that
    produced none means something broke, and reporting "no findings" would be precisely the
    quiet wrong answer this system exists not to give.
    """
    with pytest.raises(RuntimeError, match="without an answer"):
        await agent_over(
            settings, stream(("progress", {"message": "resolving the window"}))
        ).diagnose("why?")


async def test_a_session_id_is_passed_through_and_omitted_when_absent(
    settings: Settings,
) -> None:
    """§5.2's sessions live in the agent, and a question that belongs to a conversation has
    to reach it as one — but an MCP caller that has no session must not invent one."""
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=stream(("answer", ANSWER)),
            headers={"content-type": "text/event-stream"},
        )

    client = AgentClient(
        "http://agent.test",
        settings.diagnose_timeout_seconds,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await client.diagnose("why?", "session-7")
    await client.diagnose("why?")

    assert seen == [
        {"question": "why?", "session_id": "session-7"},
        {"question": "why?"},
    ]


def test_the_tool_tells_a_caller_it_cannot_act_on_the_plant() -> None:
    """§4.5 and §6.11: there is no plant-side MCP, and this binding writes nothing.

    Said in the tool description rather than only in the server instructions, because a
    runtime that shows a model one tool at a time shows it this string.
    """
    tool = diagnose_tool()

    assert tool.name == DIAGNOSE_TOOL
    assert tool.description is not None
    assert "cannot act on the plant" in tool.description
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
