"""§6.1's pipeline, in its M1 shape.

    question → extract the time phrase → resolve the window in CODE → call the tool
             → coverage is part of that result → verify citations → compose → deliver

The split at step 2 is the load-bearing one: a model reads "last hour" out of a sentence,
and code computes what it means. Date arithmetic across shift boundaries and DST is what
models are unreliable at and code is exact at.

`stream` is the pipeline; `run` drains it. There is one copy of the steps, and the
progress a reader sees is emitted from the line that does the work rather than narrated
alongside it — a progress line that can disagree with what ran is worse than none.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent.answer import Answer, Citation, Finding, Method
from agent.citations import strip
from agent.config import Settings
from agent.provider import Provider
from agent.providers_scripted import DISCLOSURE, ScriptedProvider
from agent.tools import TOOL_DEFINITIONS, AnalysisClient

SYSTEM = (
    "Answer only from tool results. Name cause and consequence separately. Cite everything. "
    "State what is missing rather than filling it. This system is read-only: it cannot act "
    "on the plant."
)

_HOURS = re.compile(
    r"last\s+(\d+|one|two|three|six|twelve|twenty-four)\s+hour", re.IGNORECASE
)
_WORDS = {"one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12, "twenty-four": 24}


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Progress:
    """One step of §7.2's visible reasoning, emitted as it happens."""

    message: str


def resolve_window(phrase: str, now: datetime) -> Window:
    """Code does the calendar maths, never the model (§6.1 step 2).

    M1 understands "last N hours" and defaults to the last hour. "Last night" needs the
    shift calendar behind §5.3's /time/resolve, which is M3 — so it is not guessed at here,
    because a wrong window is a wrong answer that looks entirely right.
    """
    match = _HOURS.search(phrase)
    hours = 1
    if match:
        token = match.group(1).lower()
        hours = _WORDS.get(token, 0) or int(token)
    return Window(start=now - timedelta(hours=hours), end=now)


def select_provider(settings: Settings) -> Provider:
    if settings.provider == "anthropic":
        from agent.providers_anthropic import AnthropicProvider

        return AnthropicProvider()
    return ScriptedProvider()


async def stream(
    question: str,
    session_id: str,
    *,
    settings: Settings | None = None,
    analysis: AnalysisClient | None = None,
    provider: Provider | None = None,
    now: datetime | None = None,
) -> AsyncIterator[Progress | Answer]:
    """Yields a `Progress` per step and exactly one `Answer`, last."""
    del session_id  # M1 keeps no session state; §5.2's tables arrive with the UI
    settings = settings or Settings()
    analysis = analysis or AnalysisClient(settings.analysis_url)
    provider = provider or select_provider(settings)
    now = now or datetime.now(UTC)

    window = resolve_window(question, now)
    yield Progress(f"resolving the window: {window.start:%H:%M}–{window.end:%H:%M} UTC")
    messages: list[dict[str, object]] = [{"role": "user", "content": question}]
    called: list[str] = []
    stats: dict[str, object] | None = None

    for _ in range(settings.tool_budget):
        reply = await provider.call(SYSTEM, messages, TOOL_DEFINITIONS)

        if reply.final is not None:
            yield Progress("verifying every citation against the database")
            yield await _compose(
                reply.final, stats, window, called, provider, analysis, question
            )
            return

        for call in reply.tool_calls:
            if call.name != "inspection_stats":
                continue
            yield Progress(f"calling {call.name}")
            stats = await analysis.inspection_stats(window.start, window.end)
            called.append(call.name)
            messages.append({"role": "tool", "name": call.name, "content": str(stats)})
            yield Progress("checking coverage over the window")

    # §6.8: budget exhaustion produces a partial answer that says what it could not finish,
    # never a silently truncated one.
    yield Answer(
        findings=[],
        answer_markdown="I ran out of tool budget before reaching an answer.",
        method=Method(
            tools_called=called,
            budget_used=settings.tool_budget,
            provider=provider.name,
        ),
        caveats=["The tool budget was exhausted; this answer is incomplete."],
    )


async def run(
    question: str,
    session_id: str,
    *,
    settings: Settings | None = None,
    analysis: AnalysisClient | None = None,
    provider: Provider | None = None,
    now: datetime | None = None,
) -> Answer:
    """`stream` without the progress, for callers that only want the answer."""
    answer: Answer | None = None
    async for item in stream(
        question,
        session_id,
        settings=settings,
        analysis=analysis,
        provider=provider,
        now=now,
    ):
        if isinstance(item, Answer):
            answer = item

    if answer is None:
        raise RuntimeError("the pipeline ended without an answer")
    return answer


async def _compose(
    final: dict[str, object],
    stats: dict[str, object] | None,
    window: Window,
    called: list[str],
    provider: Provider,
    analysis: AnalysisClient,
    question: str,
) -> Answer:
    """Step 6.5. The composer arranges; it does not author — every sentence traces to a
    finding, and every finding to a tool result."""
    del question
    method = Method(
        tools_called=called, budget_used=len(called), provider=provider.name
    )
    caveats: list[str] = []
    if provider.name == "scripted":
        caveats.append(DISCLOSURE)

    if final.get("kind") == "out_of_scope":
        return Answer(
            findings=[],
            answer_markdown=(
                "I can't change anything on the line — this system is read-only and only "
                "reads from the plant. I can show you what the line has recorded instead."
            ),
            method=method,
            caveats=caveats,
        )

    if stats is None:
        return Answer(
            findings=[],
            answer_markdown="I have no data for that window.",
            method=method,
            caveats=[*caveats, "No data was returned for the window asked about."],
        )

    total = int(str(stats.get("total", 0)))
    rejects = int(str(stats.get("rejects", 0)))
    coverage = stats.get("coverage")
    gaps = coverage.get("gaps", []) if isinstance(coverage, dict) else []
    raw_samples = stats.get("sample_serials")
    samples = raw_samples if isinstance(raw_samples, list) else []
    classes = stats.get("by_defect_class") or []

    if total == 0:
        return Answer(
            findings=[],
            answer_markdown="I have no data for that window.",
            method=method,
            caveats=[
                *caveats,
                "No parts were recorded in that window — no data, not zero rejects.",
            ],
        )

    findings = [
        Finding(
            statement=(
                f"{total} parts were inspected between {window.start:%H:%M} and "
                f"{window.end:%H:%M} UTC, of which {rejects} were rejected."
            ),
            basis="measured",
            citations=[
                Citation(kind="part", id=str(serial)) for serial in list(samples)[:3]
            ],
        )
    ]

    # §3.4's six scores are independent and do not sum to 1, so the breakdown is not a
    # partition of the rejects: a part the model believes carries two defects is counted
    # under both, and a reject no class reached the threshold for is counted under none.
    # Emitting the counts alone as `basis: "measured"` states a number under a semantics
    # the reader is never given -- so the threshold and the remainder travel with them, and
    # the remainder is what makes an empty breakdown readable rather than silent.
    threshold = stats.get("defect_class_threshold")
    unaccounted = int(str(stats.get("rejects_without_class", 0)))
    if isinstance(classes, list) and classes:
        breakdown = ", ".join(
            f"{entry['defect_class']} {entry['count']}"
            for entry in classes
            if isinstance(entry, dict)
        )
        findings.append(
            Finding(
                statement=(
                    f"By defect class, counting every class scoring {threshold} or above "
                    f"— so a part with two defects is counted twice: {breakdown}."
                ),
                basis="measured",
                citations=[],
            )
        )

    if unaccounted:
        findings.append(
            Finding(
                statement=(
                    f"{unaccounted} of those {rejects} rejects had no class scoring "
                    f"{threshold} or above, so the breakdown does not explain them."
                ),
                basis="measured",
                citations=[],
            )
        )

    # §6.1 step 3 is a guard, not a choice: a gap in the window enters the answer.
    if gaps:
        caveats.append(
            f"The window contains {len(gaps)} ingest gap(s), so these counts are incomplete."
        )

    answer = Answer(
        findings=findings,
        answer_markdown="\n\n".join(
            [*(f.statement for f in findings), *(f"_{c}_" for c in caveats)]
        ),
        method=method,
        caveats=caveats,
    )
    return await strip(answer, analysis)
