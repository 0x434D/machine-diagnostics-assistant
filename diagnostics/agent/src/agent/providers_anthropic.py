"""The real provider. Present and complete; unexercised without a key.

Imported lazily by `select_provider` so that neither the SDK nor a missing credential is
required to run, test or demo the pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from agent.provider import MODEL, ProviderReply, ToolCall

if TYPE_CHECKING:
    # Types only. The SDK itself is imported inside __init__ so that neither the package nor
    # a credential is needed to run, test or demo the pipeline.
    from anthropic.types import MessageParam, ToolParam


class AnthropicProvider:
    """Structured output via a schema-constrained tool call, which is the guarantee §6.9
    names — not a JSON-schema response format, and not prose that gets parsed."""

    name = "anthropic"

    def __init__(self) -> None:
        import anthropic  # imported here so the package is optional at runtime

        # Reads ANTHROPIC_API_KEY from the environment. The key is never held in code,
        # never logged and never written to the trace.
        self._client = anthropic.AsyncAnthropic()

    async def call(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderReply:
        response = await self._client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            messages=cast("list[MessageParam]", messages),
            tools=cast("list[ToolParam]", tools),
        )

        calls = [
            ToolCall(name=block.name, arguments=cast(dict[str, str], block.input))
            for block in response.content
            if block.type == "tool_use" and block.name != "answer"
        ]
        if calls:
            return ProviderReply(tool_calls=calls)

        final = next(
            (
                block.input
                for block in response.content
                if block.type == "tool_use" and block.name == "answer"
            ),
            None,
        )
        return ProviderReply(final=final)
