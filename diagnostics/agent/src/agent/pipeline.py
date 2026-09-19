"""§6.1's staged pipeline.

    question
       ├─ 1   classify              a model call, constrained to a fixed set of types
       ├─ 2   read the time phrase  the model reads it; /time/resolve computes the window
       ├─ 3   coverage              a guard, not a choice
       ├─ 4   route knowledge       deterministic, from the documents' own front-matter
       ├─ 5   tool loop             budgeted; every call logged
       ├─ 6   verify citations      every cited id resolved against the database
       ├─ 6.5 compose               arrange the findings into prose
       └─ 7   deliver               a structured answer object

Three of the stages are load-bearing in a way that is easy to erode, so each one is stated
here rather than only in the code below.

**Stage 2 splits deliberately.** The model reads "last night" out of a sentence and does
not compute what it means; date arithmetic across shift boundaries and DST is what models
are unreliable at and code is exact at. A phrase the calendar refuses is not guessed at —
§6.7 says to assume the most likely reading and *say so*, which is what the caveat is.

**Stage 3 is a guard.** Nothing the model does decides whether coverage runs. A window with
an ingest gap produces smaller numbers that look exactly like a quieter line, and a window
with no data at all is not a window with nothing in it.

**Stage 5 is one of the three places in this system that recovers from an error** rather
than letting it propagate. A tool error goes back to the model as a tool result, because
the model is the only thing that can work around it; after several consecutive failures the
run aborts and says so, and exhausting the budget produces a partial answer that names what
it could not finish.

`stream` is the pipeline; `run` drains it. There is one copy of the steps, and the progress
a reader sees is emitted from the line that does the work rather than narrated alongside it
— a progress line that can disagree with what ran is worse than none. §7.2's trace is
emitted the same way and for the same reason: the tool calls in it are appended by the loop
that makes them, never assembled afterwards from what the answer happens to remember.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import get_args

import httpx
from knowledge.documents import KnowledgeBase

from agent.answer import (
    ANSWER_TOOL,
    ANSWER_TOOL_NAME,
    CHART_FALLBACK,
    Answer,
    ChartType,
    Clarification,
    Contradiction,
    Finding,
    Method,
)
from agent.citations import keep, stamped
from agent.classify import Classification, classify, corrections
from agent.compose import compose
from agent.config import Settings
from agent.provider import Provider, record
from agent.providers_scripted import DISCLOSURE, ScriptedProvider
from agent.records import Budget, Timings, ToolCallRecord, Trace
from agent.routing import Selection, route
from agent.tools import TOOL_DEFINITIONS, AnalysisClient, Window

LOG = logging.getLogger(__name__)

TOOLS: list[dict[str, object]] = [*TOOL_DEFINITIONS, ANSWER_TOOL]
"""§6.1 step 5's tool set: the operations the model investigates with, and the one tool it
answers through.

One list rather than a second argument to the provider, because under §6.9 the structured
answer *is* a tool call: every turn the model chooses between reading more and answering,
and that choice is only offered if both are declared together. M4 declared only the
fifteen, so `AnthropicProvider` — which reads the final answer out of a tool call by name —
had no name to find, and a real model could not finish a run at all. Nothing went red,
because without a key nothing reaches that branch.
"""

_BUILT_IN_CHARTS = ", ".join(
    kind for kind in get_args(ChartType) if kind != CHART_FALLBACK
)

SYSTEM = (
    "Answer only from tool results and the documents below. Name cause and consequence "
    "separately. Anything that rests on a pattern is a hypothesis and carries its evidence "
    "strength. Cite everything. State what is missing rather than filling it. Recommend "
    "only documented actions. If you disagree with the computed propagation, say so and "
    "show your reasoning. This system is read-only: it cannot act on the plant. "
    f"Deliver the answer by calling the {ANSWER_TOOL_NAME} tool; what is written as a "
    "message instead of through it is not read. "
    # §7.4's vocabulary. The mechanism has existed since M6 Task 5 and nothing told the
    # model it was there, which makes a chart a feature only the scripted provider can use.
    "A chart is a citation on the finding it supports, not a picture beside it. It carries "
    f"three things: a type — one of {_BUILT_IN_CHARTS}, or {CHART_FALLBACK} with a "
    "Vega-Lite specification for a reading none of those expresses; the id of a tool call "
    "this run made, as its source; and options saying which path in that call's result "
    "holds the rows and which field each channel reads. It carries no figures of its "
    "own: the renderer draws the "
    "stored result of the call it names, so cite a call whose result holds what you want "
    "drawn, and call the tool again with the arguments you need if it does not."
)

DECLINE = (
    "I can't change anything on the line — this system is read-only and only reads from "
    "the plant. I can show you what the line has recorded instead."
)

_BASES: dict[Path, KnowledgeBase] = {}
"""One knowledge base per tree, held for the life of the process.

§6.2 hot-reloads, and a reload swaps an immutable index rather than mutating one — so the
index has to outlive a single question for there to be anything to swap. Building one per
question would re-read thirty files to arrive at the same answer and would make the reload
unobservable.
"""


@dataclass(frozen=True)
class Progress:
    """One step of §7.2's visible reasoning, emitted as it happens."""

    message: str


def _since(started: float) -> float:
    """Milliseconds since a `perf_counter()` reading."""
    return (perf_counter() - started) * 1000


@dataclass
class _Recorder:
    """What the run has done so far, accumulated by the stages that do it.

    Mutable, and passed down rather than returned up, because §6.1's pipeline leaves by
    seven different doors — declined, asked back, no data, aborted, budget exhausted,
    answered, answered after a correction — and a trace built at each of them would be seven
    constructions to keep in agreement with one run.
    """

    tool_turns_limit: int
    started: float
    sops: list[str] = field(default_factory=list)
    calls: list[ToolCallRecord] = field(default_factory=list)
    turns: int = 0
    model_ms: float = 0.0
    tools_ms: float = 0.0

    def trace(self) -> Trace:
        return Trace(
            sops_loaded=list(self.sops),
            tool_calls=list(self.calls),
            budget=Budget(
                tool_turns=self.turns, tool_turns_limit=self.tool_turns_limit
            ),
            timings=Timings(
                total_ms=_since(self.started),
                model_ms=self.model_ms,
                tools_ms=self.tools_ms,
            ),
        )


def select_provider(settings: Settings) -> Provider:
    if settings.provider == "anthropic":
        from agent.providers_anthropic import AnthropicProvider

        return AnthropicProvider()
    return ScriptedProvider()


def knowledge_base(root: Path) -> KnowledgeBase:
    base = _BASES.get(root)
    if base is None:
        base = KnowledgeBase(root)
        _BASES[root] = base
    return base


def longest(
    stops: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], list[Mapping[str, object]]]:
    """§6.7 row two: several stops in the window and a question about one of them.

    The longest, because a singular question about a window full of stops is almost always
    about the one that hurt; and the others are returned rather than discarded, because
    stating the assumption without naming what it passed over leaves the reader no way to
    redirect it.
    """
    chosen = max(stops, key=lambda stop: float(str(stop.get("duration_seconds", 0))))
    return chosen, [stop for stop in stops if stop is not chosen]


async def stream(
    question: str,
    *,
    settings: Settings | None = None,
    analysis: AnalysisClient | None = None,
    provider: Provider | None = None,
    token: str | None = None,
) -> AsyncIterator[Progress | Answer | Trace]:
    """Yields a `Progress` per step, exactly one `Answer`, and then §7.2's `Trace`.

    The trace last rather than first: it describes a run that has finished, and the reader
    on the other end of the stream is waiting for the answer, not for this. `agent.app`
    stores it before it emits the answer event, so a trace can always be fetched for an
    answer the user has been shown.

    `token` is the caller's, forwarded to the analysis service: since M5 that service
    refuses an unauthenticated request like every other one (§10.5), and the agent asks it
    questions on the asker's behalf rather than on its own.

    §5.2's `messages`, `traces` and `sessions` are written by `agent.app`, which is the
    layer that holds the principal. Nothing here reaches the database.
    """
    settings = settings or Settings()
    recorded = _Recorder(tool_turns_limit=settings.tool_budget, started=perf_counter())
    async for item in _stages(
        question,
        recorded,
        settings=settings,
        analysis=analysis,
        provider=provider,
        token=token,
    ):
        yield item
    yield recorded.trace()


async def _stages(
    question: str,
    recorded: _Recorder,
    *,
    settings: Settings,
    analysis: AnalysisClient | None,
    provider: Provider | None,
    token: str | None,
) -> AsyncIterator[Progress | Answer]:
    """§6.1's seven stages. Every exit from here is an `Answer`; `stream` adds the trace."""
    analysis = analysis or AnalysisClient(settings.analysis_url, token=token)
    provider = provider or select_provider(settings)

    caveats: list[str] = [DISCLOSURE] if provider.name == "scripted" else []

    # --- 1  classify -------------------------------------------------------------------
    yield Progress("classifying the question")
    started = perf_counter()
    classification = await classify(provider, question)
    recorded.model_ms += _since(started)
    if classification.fallback:
        caveats.append(
            "I could not classify this question, so I read it as a knowledge question. "
            "If it was about the line's data, say which part and I will look there."
        )
    caveats.extend(corrections(classification.unknown))

    # --- 4  route knowledge ------------------------------------------------------------
    # Ahead of stages 2 and 3 in wall-clock order but not in dependency order: routing needs
    # the classification and nothing else, and an out-of-scope request must be declined
    # before anything reaches for a tool (§4.5), which stage 2 already would.
    index = knowledge_base(settings.knowledge_root).reload_if_changed()
    selection = route(index, classification.facets, settings.retrieval_budget)
    recorded.sops = list(selection.ids)
    yield Progress(f"consulting {', '.join(selection.ids)}")
    if selection.dropped:
        caveats.append(
            f"{len(selection.dropped)} further document(s) matched but did not fit the "
            f"retrieval budget."
        )

    if classification.ask_back:
        yield _ask_back(question, classification, selection, caveats, provider)
        return

    if len(classification.readings) > 1:
        # §6.7: "Otherwise assume the most likely one and say so in a caveat."
        others = ", ".join(
            reading.description
            for reading in classification.readings[1:]
            if reading.description
        )
        caveats.append(
            f"I read this as {classification.readings[0].description!r}. It could also "
            f"have been read as: {others}."
        )

    if classification.question_type == "out_of_scope":
        yield Answer(
            findings=[],
            answer_markdown="\n\n".join([DECLINE, *(f"_{c}_" for c in caveats)]),
            method=Method(sops_used=list(selection.ids), provider=provider.name),
            caveats=caveats,
        )
        return

    # --- 2  the model read the phrase; code computes the window -------------------------
    window, assumption = await _window(analysis, classification, settings)
    if assumption is not None:
        caveats.append(assumption)
    yield Progress(f"the window is {window.label}")

    # --- 3  coverage -------------------------------------------------------------------
    yield Progress("checking coverage over the window")
    coverage = await analysis.coverage(window.start, window.end)
    gaps = coverage.get("gaps")
    if isinstance(gaps, list) and gaps:
        caveats.append(
            f"The window contains {len(gaps)} ingest gap(s), so anything counted over it "
            f"is incomplete."
        )
    if _observed(coverage) == 0:
        yield await _no_data(analysis, window, selection, caveats, provider)
        return

    # --- 5  the tool loop ---------------------------------------------------------------
    system = _system(selection)
    loaded = frozenset(selection.ids)
    messages: list[dict[str, object]] = [{"role": "user", "content": question}]
    called: list[str] = []
    failures = 0
    retried = False
    turns = 0

    for _ in range(settings.tool_budget):
        turns += 1
        recorded.turns = turns
        started = perf_counter()
        reply = await provider.call(system, messages, TOOLS)
        recorded.model_ms += _since(started)

        if reply.final is not None:
            # --- 6  verify citations -----------------------------------------------------
            yield Progress("verifying every citation against the database")
            # §7.3's window goes onto the citations that take one *here*, from the window
            # this run resolved at stage 2 — never from the model, which has no business
            # naming the interval its own claim is checked and opened over.
            stated = stamped(
                [
                    Finding.model_validate(finding)
                    for finding in _sequence(reply.final.get("findings"))
                ],
                window,
            )
            # §7.4's chart citations resolve against this run's own calls, by id. The
            # recorder is the single list of what was called, so a chart cannot reference
            # something the trace does not also show the reader.
            checked = await keep(
                stated,
                analysis,
                window=window,
                loaded=loaded,
                calls={made.id: made for made in recorded.calls},
            )
            if checked.failed and not retried:
                # §6.5: the failed ids go back to the model for exactly one retry. Logged
                # at the same moment, because "the failure is logged so its frequency is
                # measurable" is the half that turns this from a repair into a measurement.
                retried = True
                LOG.warning(
                    "citations failed verification", extra={"failed": checked.failed}
                )
                messages.append(
                    {"role": "user", "content": _correction(checked.failed)}
                )
                yield Progress("asking for the unverifiable citations to be corrected")
                continue

            if checked.note is not None:
                # The rate M7 scores citation integrity on is the rate of claims that were
                # *removed*, not of claims that failed once and were fixed — so the line
                # that counts is here, after the retry rather than instead of it.
                LOG.warning(
                    "citations removed from the answer",
                    extra={"failed": checked.failed, "removed": checked.removed},
                )

            yield _deliver(
                reply.final,
                checked.kept,
                [*caveats, *([checked.note] if checked.note else [])],
                selection,
                called,
                turns,
                provider,
                settings,
            )
            return

        for call in reply.tool_calls:
            yield Progress(f"calling {call.name}")
            started = perf_counter()
            try:
                result = await analysis.call(
                    call.name, call.arguments, window.start, window.end
                )
            except httpx.HTTPError as error:
                # §6.8, and one of CLAUDE.md's three sanctioned recovery sites: the model
                # is the only thing that can work around a tool that did not answer, so the
                # failure becomes something it can read rather than a 500 for the user.
                result = {
                    "error": True,
                    "detail": f"{type(error).__name__}: {error}",
                }
            elapsed = _since(started)
            failed = bool(result.get("error"))

            if call.name == "list_stops" and classification.singular:
                result, note = _one_stop(result)
                if note is not None:
                    caveats.append(note)

            # §7.2's trace, appended by the loop that made the call. The log line below
            # says the same thing to an operator tailing the service; this is the copy an
            # auditor reads back months later, and neither is derived from the other.
            #
            # **After §6.7's assumption is written into the result, not before.** The record
            # now carries the result itself (§7.4 draws charts from it), and the transcript
            # two statements down carries the same object — so recording the pre-assumption
            # copy would put a chart and an answer side by side on the screen, drawn from
            # two different readings of one call.
            recorded.calls.append(
                ToolCallRecord(
                    id=call.id,
                    name=call.name,
                    arguments=dict(call.arguments),
                    result=dict(result),
                    duration_ms=elapsed,
                    failed=failed,
                )
            )
            recorded.tools_ms += elapsed

            LOG.info(
                "tool call",
                extra={
                    "tool": call.name,
                    "arguments": dict(call.arguments),
                    "duration_ms": elapsed,
                    "failed": failed,
                },
            )
            called.append(call.name)

            record(messages, call, result)

            if not failed:
                failures = 0
                continue
            failures += 1
            if failures >= settings.tool_failure_limit:
                yield _aborted(
                    failures,
                    call.name,
                    result,
                    selection,
                    called,
                    turns,
                    caveats,
                    provider,
                )
                return

    # §6.8: budget exhaustion produces a partial answer that says what it could not finish,
    # never a silently truncated one.
    unfinished = (
        f"I ran out of tool budget after {', '.join(called) or 'no calls'} and did not "
        f"reach an answer."
    )
    exhausted = (
        f"The tool budget of {settings.tool_budget} step(s) was exhausted; this answer "
        f"is incomplete."
    )
    yield Answer(
        findings=[],
        answer_markdown="\n\n".join([unfinished, *(f"_{c}_" for c in caveats)]),
        method=Method(
            sops_used=list(selection.ids),
            tools_called=called,
            budget_used=settings.tool_budget,
            provider=provider.name,
        ),
        caveats=[*caveats, exhausted],
    )


async def run(
    question: str,
    *,
    settings: Settings | None = None,
    analysis: AnalysisClient | None = None,
    provider: Provider | None = None,
    token: str | None = None,
) -> Answer:
    """`stream` without the progress or the trace, for callers that only want the answer."""
    answer: Answer | None = None
    async for item in stream(
        question,
        settings=settings,
        analysis=analysis,
        provider=provider,
        token=token,
    ):
        if isinstance(item, Answer):
            answer = item

    if answer is None:
        raise RuntimeError("the pipeline ended without an answer")
    return answer


# --- the stages, in the order they run --------------------------------------------------


async def _window(
    analysis: AnalysisClient, classification: Classification, settings: Settings
) -> tuple[Window, str | None]:
    """Stage 2. Returns the window and, when one was assumed, the sentence that says so."""
    phrase = classification.time_phrase
    if phrase:
        window = await analysis.resolve_time(phrase)
        if window is not None:
            return window, None

    fallback = await analysis.resolve_time(settings.default_time_expression)
    if fallback is None:
        # Not recoverable and not this function's to paper over: the configured default is
        # the one expression the calendar is required to understand, and an answer computed
        # over a window nobody chose is the failure §6.1 step 2 exists to prevent.
        raise RuntimeError(
            f"the shift calendar does not understand the configured default "
            f"{settings.default_time_expression!r}"
        )

    if phrase:
        return fallback, (
            f"I could not resolve {phrase!r} against the shift calendar, so I assumed "
            f"{settings.default_time_expression!r} — {fallback.label}."
        )
    return fallback, (
        f"The question names no time expression, so I assumed "
        f"{settings.default_time_expression!r} — {fallback.label}."
    )


def _observed(coverage: Mapping[str, object]) -> int:
    """How much the window holds, from a field the contract makes required.

    Raises when it is absent rather than defaulting. A default of zero would be read as
    "nothing was recorded" and route to "I have no data for that window" — a confident
    wrong answer produced by a defence, which is the failure this system exists to refuse.
    Loud is the correct behaviour for a response that does not match its own contract.
    """
    observed = coverage.get("observed")
    events = observed.get("events") if isinstance(observed, dict) else None
    if isinstance(events, int) and not isinstance(events, bool):
        return events
    raise RuntimeError(
        f"/coverage answered without observed.events, which its contract requires: "
        f"{coverage}"
    )


async def _no_data(
    analysis: AnalysisClient,
    window: Window,
    selection: Selection,
    caveats: list[str],
    provider: Provider,
) -> Answer:
    """§6.7's last row: the window holds nothing, so offer the nearest one that does.

    "Nothing happened" and "nothing was recorded" are different answers, and only the
    second one has a next step to offer.
    """
    status = await analysis.call("line_status", {}, window.start, window.end)
    latest = status.get("latest_data_at")
    if isinstance(latest, str) and latest:
        note = (
            f"Nothing at all was recorded in {window.label}. The most recent data in the "
            f"database is at {latest} — ask again for the shift around it."
        )
    else:
        note = (
            f"Nothing at all was recorded in {window.label}, and the database holds no "
            f"data at any other time either."
        )
    caveats = [*caveats, note]
    return Answer(
        findings=[],
        answer_markdown="\n\n".join(
            ["I have no data for that window.", *(f"_{c}_" for c in caveats)]
        ),
        method=Method(
            sops_used=list(selection.ids),
            tools_called=["line_status"],
            provider=provider.name,
        ),
        caveats=caveats,
    )


def _one_stop(
    result: Mapping[str, object],
) -> tuple[dict[str, object], str | None]:
    """§6.7 row two, applied to what `/stops` returned.

    The assumption is written into the tool result rather than kept beside it, so the model
    is choosing between the same facts the caveat describes.
    """
    stops = result.get("stops")
    if not isinstance(stops, list) or len(stops) < 2:
        return dict(result), None
    entries = [stop for stop in stops if isinstance(stop, dict)]
    chosen, others = longest(entries)
    listed = ", ".join(
        f"{stop.get('id')} ({float(str(stop.get('duration_seconds', 0))):.0f} s)"
        for stop in others
    )
    return {**result, "assumed_stop": dict(chosen)}, (
        f"The window holds {len(entries)} stops and the question is about one, so I took "
        f"the longest — {chosen.get('id')}, "
        f"{float(str(chosen.get('duration_seconds', 0))):.0f} s. The others were {listed}."
    )


def _ask_back(
    question: str,
    classification: Classification,
    selection: Selection,
    caveats: list[str],
    provider: Provider,
) -> Answer:
    """§6.7's one sanctioned question back.

    Reached only when the readings lead to materially different investigations *and* none
    is clearly more likely — `Classification.ask_back` holds both halves. Asking when one
    reading was clearly more likely is scored as its own failure in §8.1, which is why the
    condition lives in one place rather than in a prompt.
    """
    readings = [
        reading.description or reading.question_type
        for reading in classification.readings
    ]
    asked = (
        f"{question.rstrip('?')}? — I can read that two ways, and they lead to different "
        f"investigations: {'; or '.join(readings)}. Which did you mean?"
    )
    return Answer(
        findings=[],
        answer_markdown="\n\n".join([asked, *(f"_{c}_" for c in caveats)]),
        method=Method(sops_used=list(selection.ids), provider=provider.name),
        caveats=caveats,
        clarification=Clarification(question=asked, readings=readings),
    )


def _aborted(
    failures: int,
    name: str,
    result: Mapping[str, object],
    selection: Selection,
    called: list[str],
    turns: int,
    caveats: list[str],
    provider: Provider,
) -> Answer:
    """§6.8: after several consecutive failures the run aborts with an honest message."""
    note = (
        f"I stopped after {failures} consecutive tool failures; the last was {name}: "
        f"{result.get('detail')}."
    )
    caveats = [*caveats, note]
    return Answer(
        findings=[],
        answer_markdown="\n\n".join(
            [
                "I could not reach the analysis service well enough to answer this.",
                *(f"_{c}_" for c in caveats),
            ]
        ),
        method=Method(
            sops_used=list(selection.ids),
            tools_called=called,
            budget_used=turns,
            provider=provider.name,
        ),
        caveats=caveats,
    )


def _deliver(
    final: Mapping[str, object],
    verified: Sequence[Finding],
    caveats: Sequence[str],
    selection: Selection,
    called: list[str],
    turns: int,
    provider: Provider,
    settings: Settings,
) -> Answer:
    """Stages 6.5 and 7: compose what verification left, and deliver it.

    Verification runs in `stream` and *before* composition, not after. §6.5 removes the
    claims whose ids do not resolve, and a claim removed from `findings` but left standing
    in the prose is the removal made invisible in the one field the reader actually reads.
    The composer's own summary needs no second pass either: its citations are inherited from
    findings that have already resolved, so it is verified by construction rather than by a
    check that could never fail.
    """
    caveats = [*caveats, *(str(caveat) for caveat in _sequence(final.get("caveats")))]
    findings, markdown = compose(
        verified, caveats, summary_after=settings.summary_after_findings
    )

    # §6.5 makes disagreement a field rather than a sentence, and an invalid one raises here
    # rather than shipping: a contradiction with no reasoning is a second unexplained verdict
    # standing beside the first, and the reader cannot choose between them.
    disagreement = final.get("contradiction")
    contradiction = (
        Contradiction.model_validate(disagreement)
        if isinstance(disagreement, dict)
        else None
    )

    return Answer(
        findings=findings,
        answer_markdown=markdown,
        method=Method(
            sops_used=list(selection.ids),
            tools_called=called,
            budget_used=turns,
            provider=provider.name,
        ),
        caveats=list(caveats),
        contradiction=contradiction,
    )


def _correction(failed: Sequence[str]) -> str:
    """§6.5's one retry, in the words it gives: *"stop:19 does not exist, correct or remove
    the claim"*.

    A plain turn rather than a forged tool result. §6.5 describes this going back "as a tool
    result", and a tool result needs a tool call to answer: writing one would put a
    `tool_use` block in the transcript claiming the model called something it never called.
    A transcript that lies about what the model did is the same quiet dishonesty every other
    guard in this pipeline exists to prevent, and the mechanism — the failure, itemised,
    back into the context for one more turn — is identical either way.
    """
    return (
        f"These citations do not resolve against the database: {', '.join(failed)}. "
        f"Correct them or "
        f"remove the claims that rest on them, then answer again. Do not cite a procedure "
        f"that was not given to you above."
    )


def _sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _system(selection: Selection) -> str:
    """The routed documents, in the system prompt rather than in the transcript.

    §6.2's method must not be something a later turn can push out of view, and the system
    prompt is the one part of the context that cannot be.
    """
    documents = "\n\n".join(
        f"## {document.id} — {document.title}\n\n{document.body}"
        for document in selection.documents
    )
    return f"{SYSTEM}\n\n# Knowledge base\n\n{documents}"
