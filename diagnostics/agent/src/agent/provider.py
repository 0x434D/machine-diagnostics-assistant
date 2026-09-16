"""§6.9's model provider layer, narrowed to what the pipeline uses.

The canonical surface is small on purpose: a system prompt, messages, tool definitions,
tool results, and a structured final answer. Two seams leak between providers — how a tool
call is represented, and how structured output is guaranteed — so they are the two things
this interface pins down rather than papers over.

Two implementations sit behind it. `ScriptedProvider` is the default and needs no
credentials; `AnthropicProvider` is the real one and is a configuration change away.

The transcript shape is Anthropic's, because one of the two had to be canonical and the
one with a real implementation is the honest choice: an assistant turn carrying `tool_use`
blocks, a user turn carrying the matching `tool_result` blocks. `record` and `results`
below are the only places that shape is written or read, so an adapter that needs a
different one has a single seam to translate at.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast

MODEL = "claude-sonnet-5"  # §6.9's default


@dataclass(frozen=True)
class ToolCall:
    """`id` is what pairs a result with the call that asked for it. Providers that have no
    such identifier generate one; the pipeline never invents it, because a transcript whose
    results are matched by position is one reordering away from a wrong answer."""

    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ProviderReply:
    """Either a request to call tools, or the final structured answer — never prose that
    something downstream has to parse."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    final: dict[str, object] | None = None


class Provider(Protocol):
    name: str

    async def call(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderReply: ...


def record(
    messages: list[dict[str, object]],
    call: ToolCall,
    result: Mapping[str, object],
) -> None:
    """Append the call and its result to the transcript, in the canonical shape.

    The result rides on a **user** turn, which is what lets tool turns live under §5.2's
    `messages.role` CHECK of ('user','assistant') without a migration. One thing is lost and
    is worth knowing before M5 persists transcripts: at the database level a synthetic
    tool-result turn is then indistinguishable from something an operator typed. Nothing
    reads it that way today — only `results` below parses these, and it looks for
    `tool_result` blocks — but a later reader counting "what the user said" would count
    these, and that is a trap rather than a defect.
    """
    messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": dict(call.arguments),
                }
            ],
        }
    )
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(dict(result), default=str),
                }
            ],
        }
    )


def results(messages: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    """Every tool result in the transcript, by tool name, latest wins.

    Latest wins because a tool called twice was called the second time for a reason. A
    provider reading its own transcript is how the scripted one stands in for a model
    without being handed a private channel the real one would not have.
    """
    names: dict[str, str] = {}
    out: dict[str, dict[str, object]] = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                names[str(block.get("id"))] = str(block.get("name"))
            elif kind == "tool_result":
                name = names.get(str(block.get("tool_use_id")))
                if name is not None:
                    out[name] = cast(
                        dict[str, object], json.loads(str(block.get("content")))
                    )
    return out
