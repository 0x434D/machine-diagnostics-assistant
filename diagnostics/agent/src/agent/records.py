"""§5.2's two records that hang off an answered message: how it was reached, and what the
operator thought of the answer.

`agent.sessions` stores them and `agent.app` serves them, and the same models travel in both
directions. A trace read back out of `agent.traces` is validated against the shape the
pipeline wrote, so a field the pipeline stops recording fails on the next read rather than
reaching the UI as a key that is quietly absent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    """One call the tool loop made: what was asked of which tool, and what it cost.

    The arguments and not only the name, because §7.2 is explicit about what the trace buys
    — it makes the "did it follow the method" axis checkable by eye — and `list_stops` on
    its own says nothing about which station or which window was asked for.

    A record rather than a `ToolCall`: `provider.ToolCall` is the request the model made,
    and this is what became of it.
    """

    name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    duration_ms: float

    failed: bool
    """The tool answered with an error. §6.8 hands those back to the model as tool results
    rather than raising, so a run that worked around three failures is indistinguishable in
    `Method.tools_called` from one whose three calls all succeeded. Here it is not."""


class Budget(BaseModel):
    """§6.1 step 5's budget: turns of the tool loop, against the ceiling they ran under.

    Turns rather than calls, for the reason `answer.Method` gives in more words: one turn
    may carry several calls, and one field carrying two units is a number nobody can compare
    across two answers.
    """

    tool_turns: int
    tool_turns_limit: int


class Timings(BaseModel):
    """Where the time went.

    Three numbers rather than a stage-by-stage breakdown. The question a reader asks of a
    slow answer is whether the model or the analysis service took it; the remainder is the
    pipeline's own work — routing, composition, citation verification — which has never been
    the part that takes the time. When it becomes so it gets its own field here, rather than
    being derived by subtraction from these.
    """

    total_ms: float
    model_ms: float
    tools_ms: float


class Trace(BaseModel):
    """§7.2's reasoning trace, in the shape `agent.traces` holds it."""

    sops_loaded: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    budget: Budget
    timings: Timings


class Feedback(BaseModel):
    """§7.2's two questions, and the comment beside them.

    Both questions are independently answerable and `agent.feedback` is nullable in both
    columns for exactly that reason: `None` is *not answered*, which is a different claim
    from *no*. One model in both directions, so answering one question now and the other one
    later is two requests rather than a form that has to be filled in at once.

    **Nothing here withdraws an answer.** §7.2 asks two questions and offers no third state
    to move back to, and a `null` that cleared a stored answer would make the ordinary case
    — submitting the second question without resending the first — erase the first.
    """

    useful: bool | None = None
    matched_reality: bool | None = None
    comment: str | None = None
