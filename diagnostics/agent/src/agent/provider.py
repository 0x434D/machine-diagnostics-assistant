"""§6.9's model provider layer, narrowed to what M1 uses.

The canonical surface is small on purpose: a system prompt, messages, tool definitions,
tool results, and a structured final answer. Two seams leak between providers — how a tool
call is represented, and how structured output is guaranteed — so they are the two things
this interface pins down rather than papers over.

M1 ships two implementations behind it. `ScriptedProvider` is the default and needs no
credentials; `AnthropicProvider` is the real one and is a configuration change away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

MODEL = "claude-sonnet-5"  # §6.9's default


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, str]


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
