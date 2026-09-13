"""A provider that does not think, and says so.

M1 ships this as the default so the whole pipeline — routing, the tool loop, citation
verification against the real database, the answer object, composition — runs and is
tested without credentials. It is the same move §3.4 makes for the vision system, where
`SimulatedClassifier` sits behind the interface a real model would use and "the signature
would not change".

Scripted and deterministic on purpose: the same question produces the same tool calls and
the same answer every time, so the evaluation harness has a fixture it can rely on and
nobody can mistake the output for reasoning. What it demonstrably does NOT test is whether
a model would choose those tools or draw those conclusions.
"""

from __future__ import annotations

from agent.provider import ProviderReply, ToolCall

#: Written into every answer this provider produces, in the prose a reader sees and not
#: only in the trace. A simulated answer that reads like a real one is exactly the quiet
#: wrong answer this system exists not to give.
DISCLOSURE = (
    "This answer was produced by the scripted provider, not by a model. The figures and "
    "citations are real — they come from the database — but the reasoning is fixed."
)


class ScriptedProvider:
    """Needs no credentials and reaches no network."""

    name = "scripted"

    def __init__(self) -> None:
        self._turn = 0

    async def call(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderReply:
        del system, tools
        self._turn += 1

        question = str(messages[0].get("content", "")).lower() if messages else ""

        if _is_out_of_scope(question):
            # §6.1/§4.5: nothing crosses towards the plant. Declined before any tool runs.
            return ProviderReply(final={"kind": "out_of_scope"})

        if self._turn == 1:
            return ProviderReply(
                tool_calls=[ToolCall(name="inspection_stats", arguments={})]
            )

        return ProviderReply(final={"kind": "stats"})


def _is_out_of_scope(question: str) -> bool:
    """Requests to act on the line. Kept as a list of verbs rather than a model call
    because M1's provider is not a model, and pretending otherwise would be the
    dishonesty this whole file exists to avoid."""
    return any(
        verb in question
        for verb in (
            "increase",
            "decrease",
            "set ",
            "change",
            "restart",
            "stop the",
            "acknowledge",
        )
    )
