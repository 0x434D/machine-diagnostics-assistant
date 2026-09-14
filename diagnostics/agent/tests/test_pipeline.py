"""§6.1's pipeline, and the guards that make its answers trustworthy."""

from __future__ import annotations

from datetime import UTC, datetime

from agent.answer import Answer
from agent.config import Settings
from agent.pipeline import Progress, resolve_window, run, stream
from agent.providers_scripted import DISCLOSURE, ScriptedProvider
from agent.tools import AnalysisClient

from .fakes import FakeAnalysis, stats

NOW = datetime(2026, 9, 12, 14, 30, tzinfo=UTC)


def _client(fake: FakeAnalysis) -> AnalysisClient:
    return fake  # type: ignore[return-value]


def test_time_windows_are_computed_in_code_not_by_the_model() -> None:
    """§6.1 step 2: the model reads "last hour" out of the sentence; code does the calendar
    maths, because date arithmetic is what models are unreliable at."""
    window = resolve_window("last hour", now=NOW)

    assert window.start == datetime(2026, 9, 12, 13, 30, tzinfo=UTC)
    assert window.end == NOW


async def test_a_window_with_gaps_forces_a_caveat(
    fake_analysis_with_gap: FakeAnalysis,
) -> None:
    """§6.1 step 3 is a guard, not a choice: if the window has gaps, the answer says so."""
    answer = await run(
        "how many rejects in the last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis_with_gap),
        provider=ScriptedProvider(),
        now=NOW,
    )

    assert any(
        "gap" in caveat.lower() or "incomplete" in caveat.lower()
        for caveat in answer.caveats
    )


async def test_an_empty_window_says_so_instead_of_inventing(
    fake_analysis_empty: FakeAnalysis,
) -> None:
    """§1's agent proof: "I have no data for that window" rather than an invented answer.
    No parts recorded is not the same claim as zero rejects."""
    answer = await run(
        "how many rejects last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis_empty),
        provider=ScriptedProvider(),
        now=NOW,
    )

    assert answer.findings == []
    assert any(
        "no data" in caveat.lower() or "no parts" in caveat.lower()
        for caveat in answer.caveats
    )


async def test_out_of_scope_requests_are_declined(fake_analysis: FakeAnalysis) -> None:
    """§4.5: nothing crosses towards the plant. Declined before any tool runs."""
    answer = await run(
        "increase the joining force at S2",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis),
        provider=ScriptedProvider(),
        now=NOW,
    )

    assert "read-only" in answer.answer_markdown.lower()
    assert answer.findings == []
    assert fake_analysis.calls == 0, "an out-of-scope request reached a tool"


async def test_an_answer_reports_real_counts_and_cites_a_real_serial(
    fake_analysis: FakeAnalysis,
) -> None:
    answer = await run(
        "how many rejects in the last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis),
        provider=ScriptedProvider(),
        now=NOW,
    )

    assert "600" in answer.answer_markdown
    assert "30" in answer.answer_markdown
    assert answer.findings[0].citations[0].id == "A-00000007"
    assert all(finding.basis == "measured" for finding in answer.findings)


async def test_the_defect_breakdown_carries_the_semantics_it_was_counted_under(
    fake_analysis: FakeAnalysis,
) -> None:
    """A count stated as `basis: "measured"` under a rule the reader is not given is a
    number that will be read as something else.

    §3.4's six scores are independent and do not sum to 1, so the breakdown is not a
    partition of the rejects — a part with two defects is counted twice. The threshold it
    was counted at is in the response for exactly this reason, and dropping it here would
    put the endpoint's one consumer back where the endpoint was.
    """
    answer = await run(
        "how many rejects in the last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis),
        provider=ScriptedProvider(),
        now=NOW,
    )

    breakdown = next(f for f in answer.findings if "defect class" in f.statement)
    assert "0.5 or above" in breakdown.statement
    assert "counted twice" in breakdown.statement


async def test_rejects_no_class_explains_are_reported_rather_than_left_out() -> None:
    """§3.5 scenario 6 is a window of exactly these: every class present and every one of
    them decayed below the threshold. Silently, that window reads as "no defects seen"
    while the line scraps parts."""
    answer = await run(
        "how many rejects in the last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(
            FakeAnalysis(
                stats(total=600, rejects=30, gaps=[], rejects_without_class=12)
            )
        ),
        provider=ScriptedProvider(),
        now=NOW,
    )

    unaccounted = next(f for f in answer.findings if "no class scoring" in f.statement)
    assert "12 of those 30 rejects" in unaccounted.statement
    assert unaccounted.basis == "measured"


async def test_a_window_the_breakdown_fully_explains_says_nothing_extra(
    fake_analysis: FakeAnalysis,
) -> None:
    """The other direction: on an ordinary window nothing is unaccounted for, and a finding
    claiming otherwise would be noise the reader has to learn to ignore."""
    answer = await run(
        "how many rejects in the last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis),
        provider=ScriptedProvider(),
        now=NOW,
    )

    assert not any("no class scoring" in f.statement for f in answer.findings)


async def test_a_scripted_answer_says_it_was_not_produced_by_a_model(
    fake_analysis: FakeAnalysis,
) -> None:
    """The disclosure is in the prose a reader sees, not only in the trace. A simulated
    answer that reads like a real one is exactly the quiet wrong answer this system exists
    not to give."""
    answer = await run(
        "how many rejects in the last hour?",
        "s1",
        settings=Settings(),
        analysis=_client(fake_analysis),
        provider=ScriptedProvider(),
        now=NOW,
    )

    assert answer.method.provider == "scripted"
    assert DISCLOSURE in answer.caveats
    assert "scripted provider" in answer.answer_markdown


async def test_the_pipeline_reports_each_step_before_the_answer(
    fake_analysis: FakeAnalysis,
) -> None:
    """§7.2: the reasoning is shown, not hidden behind a spinner. Every tool the pipeline
    ran is named on the wire while it runs, and the answer is the last thing to arrive."""
    items = [
        item
        async for item in stream(
            "how many rejects in the last hour?",
            "s1",
            settings=Settings(),
            analysis=_client(fake_analysis),
            provider=ScriptedProvider(),
            now=NOW,
        )
    ]

    assert isinstance(items[-1], Answer), "the answer is not the last item"
    progress = [item.message for item in items if isinstance(item, Progress)]
    assert len(items) == len(progress) + 1, "more than one answer was streamed"
    assert any("inspection_stats" in line for line in progress)
    assert any("citation" in line for line in progress)
