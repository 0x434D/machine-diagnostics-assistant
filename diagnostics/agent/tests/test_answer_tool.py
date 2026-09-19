"""The tool the model answers through, and the branch that reads it.

M6 found that nothing declared it. `providers_anthropic` turns a reply into a final answer
only when the reply carries a tool call by one particular name, and no tool by that name
was ever offered to the model — so with a real key the pipeline could not produce an answer
at all, chart or otherwise. Nothing went red, for the reason that keeps producing this
defect in this project: the check that would have caught it is not in the path that
decides. `ScriptedProvider` builds its `final` itself and never consults the tool list, and
it is what every test, demo and evaluation run has used.

**What these tests can prove, without a key:** that the tool the pipeline passes declares
the committed answer contract rather than a copy of it, and that a reply naming that tool
reaches the final-answer branch of the real provider's own `call` — over a stubbed
transport, so the code under test is the provider's and only the HTTP exchange is fake.

**What they cannot:** that the API accepts this schema, that a model asked a plant question
would call this tool, or that what it would put in it verifies. Those need a key, and until
there is one no claim about them belongs in this repository. See
`tests/test_pipeline.py`'s table for the same division applied to the rest of §6.1.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from agent.answer import ANSWER_TOOL
from agent.pipeline import TOOLS
from agent.provider import ProviderReply
from agent.providers_anthropic import AnthropicProvider

CONTRACT = Path(__file__).resolve().parents[3] / "contracts" / "answer.schema.json"

FINAL: dict[str, object] = {
    "answer_markdown": "600 parts, 30 rejected.",
    "method": {"tools_called": ["inspection_stats"], "provider": "anthropic"},
}
"""What the model would have put in the call. Its content is not what is under test here —
`test_pipeline.py` covers what the pipeline does with a final — only that it arrives."""


def committed_schema() -> dict[str, object]:
    """The contract, minus the one key that describes the file rather than the shape."""
    committed: dict[str, object] = json.loads(CONTRACT.read_text())
    return {key: value for key, value in committed.items() if key != "$schema"}


class _Messages:
    """`client.messages`, stubbed at the one method the provider calls."""

    def __init__(self, content: list[SimpleNamespace]) -> None:
        self._content = content
        self.tools: list[Mapping[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        tools = kwargs.get("tools")
        assert isinstance(tools, list)
        self.tools = tools
        return SimpleNamespace(content=self._content)


def _provider(
    monkeypatch: pytest.MonkeyPatch, blocks: list[SimpleNamespace]
) -> AnthropicProvider:
    """The real provider over a stubbed SDK client.

    The client is replaced where the provider builds it rather than after the fact, so no
    credential is read and nothing reaches the network — and the provider under test is the
    one `select_provider` returns, constructor included.
    """
    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda: SimpleNamespace(messages=_Messages(blocks))
    )
    return AnthropicProvider()


def _tool_use(name: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=f"toolu_{name}", name=name, input=FINAL)


async def _reply_to(monkeypatch: pytest.MonkeyPatch, name: str) -> ProviderReply:
    """What the provider makes of a turn whose one tool call is `name`."""
    return await _provider(monkeypatch, [_tool_use(name)]).call(
        "system", [{"role": "user", "content": "how many rejects last night?"}], TOOLS
    )


def test_the_answer_tool_declares_the_contract_rather_than_a_copy_of_it() -> None:
    """§10.1: contracts/ is the source of truth, and this is the second reader of it.

    The committed file is generated from the same `json_schema()` this tool is built from
    and `test_contract.py` holds the two together, so the chain runs contract → schema →
    tool with nothing hand-written in it. A schema typed out beside the model would drift
    the way only prompts drift: silently, and discovered by someone wondering why a field
    keeps coming back missing.
    """
    assert ANSWER_TOOL["input_schema"] == committed_schema()


async def test_exactly_one_tool_the_pipeline_passes_reaches_the_final_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property the missing declaration violated, asserted end to end over the real
    provider's own branching rather than by searching the list for a name.

    Every tool the pipeline offers is put to the provider as the model's chosen call. One
    of them must come back as a finished answer and the rest as calls to make; if the
    answer tool is dropped from the list, or the branch that reads it is renamed on either
    side, there is no such tool and this fails. A test that looked for the string `answer`
    in `TOOLS` would pass in both of those cases.
    """
    finals = []
    for tool in TOOLS:
        name = str(tool["name"])
        reply = await _reply_to(monkeypatch, name)
        if reply.final is None:
            assert [call.name for call in reply.tool_calls] == [name]
        else:
            assert reply.tool_calls == []
            finals.append((tool, reply))

    assert len(finals) == 1, (
        f"{len(finals)} of the {len(TOOLS)} tools the pipeline passes end the run; "
        f"§6.3 has exactly one, and a run has no way to finish without it"
    )
    tool, reply = finals[0]
    # And it is the one carrying §6.3's answer object, so a tool that ends the run by
    # accident — a renamed operation, say — is not mistaken for the one that means to.
    assert tool["input_schema"] == committed_schema()
    assert reply.final == FINAL


async def test_a_turn_that_calls_nothing_is_neither_a_call_nor_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that writes prose instead of calling the tool has not answered.

    §6.3 is *"nothing is parsed out of prose"*, so the text of such a turn is dropped and
    the loop spends a turn — §6.1 step 5's budget is what bounds that, and exhausting it
    produces §6.8's partial answer that says what it could not finish. Asserted because the
    alternative reading of an empty reply — treating the text as the answer — is the one
    thing §6.3 forbids, and it would be a one-line change away.
    """
    provider = _provider(
        monkeypatch, [SimpleNamespace(type="text", text="the line stood twice")]
    )
    reply = await provider.call("system", [], TOOLS)

    assert reply.final is None
    assert reply.tool_calls == []
