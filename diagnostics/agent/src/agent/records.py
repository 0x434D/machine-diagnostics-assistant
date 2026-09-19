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
    """One call the tool loop made: what was asked of which tool, what came back, what it cost.

    The arguments and not only the name, because §7.2 is explicit about what the trace buys
    — it makes the "did it follow the method" axis checkable by eye — and `list_stops` on
    its own says nothing about which station or which window was asked for.

    A record rather than a `ToolCall`: `provider.ToolCall` is the request the model made,
    and this is what became of it.
    """

    id: str
    """`provider.ToolCall.id` — the identifier that paired this result with the call that
    asked for it, kept so that §7.4's chart citations can reference it. A chart names a tool
    call and never a tool: one run may call `inspection_stats` three times with three
    arguments, and a name would resolve to whichever of the three was looked at first."""

    name: str
    arguments: dict[str, object] = Field(default_factory=dict)

    result: dict[str, object]
    """**What the tool actually answered, kept verbatim.** §7.4: *"the data always comes
    from a verified tool result"* — so a chart is drawn from this and from nothing else, and
    the copy that is drawn has to be the copy the model reasoned over. Re-querying the
    analysis service at render time would be a second read, minutes later, that can disagree
    with the first: the reader would then be shown a chart of figures the answer above it was
    never based on, which is the failure §7.4 describes with the invention removed and the
    authority left in place.

    Required rather than defaulted, for the reason `agent.sessions.trace_of` gives: a run
    that stopped recording this must fail on the next read rather than reach the UI as a key
    that is quietly absent, and a chart with no rows is exactly the empty axis that reads as
    zero."""

    duration_ms: float

    failed: bool
    """The tool answered with an error. §6.8 hands those back to the model as tool results
    rather than raising, so a run that worked around three failures is indistinguishable in
    `Method.tools_called` from one whose three calls all succeeded. Here it is not — and
    §7.4 turns on it: a chart may reference a call that succeeded and no other."""


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


#: The same two decorations `agent.answer` applies to its own contract, for the same reason:
#: a file decorated by hand after generation is a file nothing can compare against.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_TITLE = "machine-agent reasoning trace (§7.2)"
FEEDBACK_TITLE = "machine-agent answer feedback (§7.2)"


def json_schema() -> dict[str, object]:
    """§7.2's trace as JSON Schema, committed to `contracts/` and generated from there.

    It earned a contract at M6. Before this the trace was something the UI showed a summary
    of out of `Answer.method`; §7.4 makes it something the UI *reads data out of* — a chart
    references a tool call by id and the result is here — and a hand-written TypeScript type
    for that would assert the shape of somebody else's response with full confidence and no
    way to be wrong out loud, which is the one thing `ui/src/api.ts` refuses to do.
    """
    return {
        "$schema": SCHEMA_DIALECT,
        **Trace.model_json_schema(),
        "title": SCHEMA_TITLE,
    }


def feedback_json_schema() -> dict[str, object]:
    """`Feedback` as JSON Schema, for the same reason the trace has one.

    The endpoint answers with **everything now stored** for the message rather than with an
    echo of the request, because that is what lets a client that answered one question render
    both without asking again — so the response is a shape the UI reads rather than a status
    it ignores, and the three-state `bool | None` in it is exactly the distinction a
    hand-written `boolean` would flatten.
    """
    return {
        "$schema": SCHEMA_DIALECT,
        **Feedback.model_json_schema(),
        "title": FEEDBACK_TITLE,
    }
