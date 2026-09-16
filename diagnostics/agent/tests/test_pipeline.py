"""§6.1's staged pipeline, and the guards that make its answers trustworthy.

The stages are tested through the pipeline rather than one by one, because what each stage
must guarantee is a property of the answer: that the window came from the calendar and not
from the model, that coverage was consulted whether or not anything asked it to, that a
tool that failed reached the model instead of the user, and that nothing the reader sees is
a sentence the composer made up.
"""

from __future__ import annotations

import logging

import pytest
from agent.answer import Answer
from agent.config import Settings
from agent.pipeline import Progress, longest, run, stream
from agent.providers_scripted import DISCLOSURE, ScriptedProvider
from agent.tools import AnalysisClient

from .fakes import (
    FakeAnalysis,
    coverage,
    line_status,
    stats,
    stop,
    stop_detail,
    stop_list,
)

STATS_QUESTION = "how many rejects in the last hour?"
STOP_QUESTION = "why did the line stand last night?"


def _client(fake: FakeAnalysis) -> AnalysisClient:
    return fake  # type: ignore[return-value]


async def _run(question: str, fake: FakeAnalysis, **kwargs: object) -> Answer:
    return await run(
        question,
        "s1",
        settings=Settings(**kwargs),  # type: ignore[arg-type]
        analysis=_client(fake),
        provider=ScriptedProvider(),
    )


def _stop_fake(*stops: dict[str, object], **kwargs: object) -> FakeAnalysis:
    return FakeAnalysis(
        {
            "list_stops": stop_list(*stops),
            "get_stop": stop_detail("s-1", 660.0),
            **{key: value for key, value in kwargs.items()},
        }
    )


# --- stage 2: the model reads the phrase, code computes the window ---------------------


async def test_the_window_is_computed_by_the_calendar_and_not_by_the_agent(
    fake_analysis: FakeAnalysis,
) -> None:
    """§6.1 step 2. The phrase the model read goes to /time/resolve, and the window the
    tools run over is the one that came back — date arithmetic across shift boundaries and
    DST is what models are unreliable at and code is exact at."""
    await _run(STATS_QUESTION, fake_analysis)

    assert ("resolve_time", {"expression": "last hour"}) in fake_analysis.calls
    assert fake_analysis.windows[0][0].isoformat() == "2026-09-12T13:30:00+00:00"


async def test_last_night_is_a_shift_and_not_eight_hours_of_arithmetic() -> None:
    """The phrase M1's regex could not read, and the reason the split exists."""
    fake = _stop_fake(stop("s-1", 660.0))

    await _run(STOP_QUESTION, fake)

    assert ("resolve_time", {"expression": "last night"}) in fake.calls


async def test_an_unresolvable_phrase_is_assumed_and_said_rather_than_guessed() -> None:
    """§6.7's first row. The calendar answers 422 rather than a near-miss window, so the
    agent falls back to the configured default *and states it* — a window silently
    substituted is every number in the answer computed over a different question."""
    fake = FakeAnalysis({"inspection_stats": stats(total=600, rejects=30, gaps=[])})

    answer = await _run("how many rejects during the audit?", fake)

    assert any("this shift" in caveat for caveat in answer.caveats)
    assert any("assum" in caveat.lower() for caveat in answer.caveats)


async def test_no_time_expression_at_all_assumes_the_current_shift_and_says_so(
    fake_analysis: FakeAnalysis,
) -> None:
    """§6.7 row one, the other half: there is nothing to resolve, so the assumption is the
    current shift and the reader is told."""
    answer = await _run("how many rejects?", fake_analysis)

    assert any("this shift" in caveat for caveat in answer.caveats)


# --- stage 3: coverage is a guard, not a choice ---------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        STATS_QUESTION,
        STOP_QUESTION,
        "what is happening right now?",
        "what does contamination mean?",
    ],
)
async def test_coverage_is_consulted_for_every_question_that_reaches_the_data(
    question: str,
) -> None:
    """§6.1 step 3 is a guard, not a choice. Nothing in the model's hands decides whether
    it runs, which is what this asserts: no question type can get past it."""
    fake = _stop_fake(
        stop("s-1", 660.0),
        inspection_stats=stats(total=600, rejects=30, gaps=[]),
        line_status=line_status(),
    )

    await _run(question, fake)

    assert "coverage" in fake.names


async def test_coverage_runs_before_any_tool_the_model_chose(
    fake_analysis: FakeAnalysis,
) -> None:
    """A gap discovered after the fact is a correction; a gap known before the tool loop is
    context the answer is built on."""
    await _run(STATS_QUESTION, fake_analysis)

    assert fake_analysis.names.index("coverage") < fake_analysis.names.index(
        "inspection_stats"
    )


async def test_a_window_with_gaps_forces_a_caveat(
    fake_analysis_with_gap: FakeAnalysis,
) -> None:
    """§6.1 step 3: if the window has gaps, the answer says so."""
    answer = await _run(STATS_QUESTION, fake_analysis_with_gap)

    assert any(
        "gap" in caveat.lower() or "incomplete" in caveat.lower()
        for caveat in answer.caveats
    )


async def test_a_window_with_no_data_at_all_offers_the_nearest_one_that_has_some() -> (
    None
):
    """§6.7's last row. "Nothing happened" and "nothing was recorded" are different answers
    and the second one has a next step."""
    fake = FakeAnalysis({"coverage": coverage(events=0), "line_status": line_status()})

    answer = await _run(STATS_QUESTION, fake)

    assert answer.findings == []
    assert any("2026-09-12T14:29" in caveat for caveat in answer.caveats)
    assert "line_status" in fake.names


async def test_an_empty_window_says_so_instead_of_inventing(
    fake_analysis_empty: FakeAnalysis,
) -> None:
    """§1's agent proof: "I have no data for that window" rather than an invented answer.
    No parts recorded is not the same claim as zero rejects."""
    answer = await _run(STATS_QUESTION, fake_analysis_empty)

    assert answer.findings == []
    assert any(
        "no data" in caveat.lower() or "no parts" in caveat.lower()
        for caveat in answer.caveats
    )


# --- stage 1: classification, and what it drives --------------------------------------


async def test_the_classification_drives_which_procedure_is_loaded() -> None:
    """Stage 1 feeds stage 4: the type *is* the routing facet, so a stop question arrives
    with the stop procedure and the always-loaded method."""
    fake = _stop_fake(stop("s-1", 660.0))

    answer = await _run(STOP_QUESTION, fake)

    assert {"CORE-01", "CORE-02", "SOP-01"} <= set(answer.method.sops_used)


async def test_an_unclassifiable_question_falls_back_with_a_caveat(
    fake_analysis: FakeAnalysis,
) -> None:
    """§6.1: "An unclassifiable question falls back to `knowledge` with a caveat rather
    than guessing." The caveat is the point — a silent fallback is a guess."""
    answer = await _run("qwerty zxcvb", fake_analysis)

    assert any("could not" in caveat.lower() for caveat in answer.caveats)


async def test_out_of_scope_requests_are_declined(fake_analysis: FakeAnalysis) -> None:
    """§4.5: nothing crosses towards the plant. Declined before any tool runs."""
    answer = await _run("increase the joining force at S2", fake_analysis)

    assert "read-only" in answer.answer_markdown.lower()
    assert answer.findings == []
    assert fake_analysis.calls == [], "an out-of-scope request reached a tool"


# --- §6.7, both directions ------------------------------------------------------------


async def test_a_question_with_two_different_investigations_asks_back(
    fake_analysis: FakeAnalysis,
) -> None:
    """§6.7: ask only when the readings lead to materially different investigations *and*
    no reading is clearly more likely. This is that case, and asking is correct."""
    answer = await _run("why are we losing parts?", fake_analysis)

    assert answer.clarification is not None
    assert len(answer.clarification.readings) == 2
    assert answer.findings == []
    assert fake_analysis.calls == [], (
        "the agent investigated a question it had to ask about"
    )


async def test_a_question_that_only_looks_ambiguous_is_answered(
    fake_analysis: FakeAnalysis,
) -> None:
    """§8.1's own evaluation class, and the mirror of the test above: looks ambiguous, is
    not. **Asking here is the failure.**"""
    answer = await _run("why are we losing parts to scrap?", fake_analysis)

    assert answer.clarification is None
    assert answer.findings


async def test_several_stops_in_one_window_take_the_longest_and_list_the_others() -> (
    None
):
    """§6.7 row two. The question is singular and the window is not; the assumption is
    stated and what it passed over is named, so the reader can redirect it."""
    fake = _stop_fake(stop("s-1", 660.0), stop("s-2", 120.0), stop("s-3", 45.0))

    answer = await _run(STOP_QUESTION, fake)

    assumption = next(caveat for caveat in answer.caveats if "longest" in caveat)
    assert "s-2" in assumption
    assert "s-3" in assumption
    assert ("get_stop", {"identifier": "s-1"}) in fake.calls


async def test_something_the_line_does_not_have_is_corrected_rather_than_asked_about() -> (
    None
):
    """§6.7 row three: "correct it and name what does — that is answering, not asking"."""
    fake = FakeAnalysis({"line_status": line_status()})

    answer = await _run("what is station S9 doing right now?", fake)

    assert answer.clarification is None
    correction = next(caveat for caveat in answer.caveats if "S9" in caveat)
    assert "S4" in correction


def test_the_longest_stop_is_picked_by_duration_not_by_order() -> None:
    chosen, others = longest(
        [stop("s-1", 45.0), stop("s-2", 660.0), stop("s-3", 120.0)]
    )

    assert chosen["id"] == "s-2"
    assert [entry["id"] for entry in others] == ["s-1", "s-3"]


# --- stage 5: the tool loop -----------------------------------------------------------


async def test_a_tool_error_goes_back_to_the_model_rather_than_crashing() -> None:
    """§6.8, and one of CLAUDE.md's three sanctioned recovery sites. The service refuses
    once; the model sees the refusal, calls again, and the answer still arrives."""
    fake = FakeAnalysis(
        {"inspection_stats": stats(total=600, rejects=30, gaps=[])},
        flaky={"inspection_stats": "the window is being rebuilt"},
    )

    answer = await _run(STATS_QUESTION, fake)

    assert answer.findings
    assert answer.method.tools_called == ["inspection_stats", "inspection_stats"]


async def test_a_transport_failure_is_a_tool_result_and_not_an_exception() -> None:
    """The one exception this pipeline catches, and the only place it has a recovery: a
    connection that never answered becomes something the model can read and retry."""
    fake = FakeAnalysis(
        {"inspection_stats": stats(total=600, rejects=30, gaps=[])},
        flaky_raises={"inspection_stats"},
    )

    answer = await _run(STATS_QUESTION, fake)

    assert answer.findings
    assert answer.method.tools_called == ["inspection_stats", "inspection_stats"]


async def test_several_consecutive_failures_abort_with_an_honest_message() -> None:
    """§6.8: after several consecutive failures the run aborts. Honestly means the reader
    is told the run stopped, not handed a confident answer built on nothing."""
    fake = FakeAnalysis(errors={"inspection_stats": "the analysis service is unwell"})

    answer = await _run(STATS_QUESTION, fake, tool_failure_limit=2)

    assert answer.findings == []
    assert any("consecutive" in caveat.lower() for caveat in answer.caveats)


async def test_budget_exhaustion_names_what_it_could_not_finish(
    fake_analysis: FakeAnalysis,
) -> None:
    """§6.8: a partial answer that says what it could not finish, never a silently
    truncated one."""
    answer = await _run(STATS_QUESTION, fake_analysis, tool_budget=1)

    assert any("budget" in caveat.lower() for caveat in answer.caveats)
    assert "inspection_stats" in answer.answer_markdown


async def test_every_tool_call_is_logged_with_its_arguments_and_its_timing(
    fake_analysis: FakeAnalysis, caplog: pytest.LogCaptureFixture
) -> None:
    """§6.1 step 5: "budgeted; every call logged". §5.2's `traces` table is where these
    land per session; the log is what makes them visible without one."""
    with caplog.at_level(logging.INFO, logger="agent.pipeline"):
        await _run(STATS_QUESTION, fake_analysis)

    record = next(r for r in caplog.records if r.msg == "tool call")
    # The fields ride on the record via `extra`, which is how a structured log carries
    # anything a formatter did not already know about; LogRecord has no attributes for them.
    assert getattr(record, "tool") == "inspection_stats"  # noqa: B009
    assert getattr(record, "arguments") == {}  # noqa: B009
    assert getattr(record, "duration_ms") >= 0  # noqa: B009


async def test_the_tool_loop_can_reach_more_than_one_operation() -> None:
    """M1 called one hardcoded tool. A stop investigation now walks the list and then the
    detail, which is the shape SOP-01 describes."""
    fake = _stop_fake(stop("s-1", 660.0))

    answer = await _run(STOP_QUESTION, fake)

    assert answer.method.tools_called == ["list_stops", "get_stop"]


# --- stage 6.5: the composer, and what it is not allowed to do ------------------------


async def test_an_answer_reports_real_counts_and_cites_a_real_serial(
    fake_analysis: FakeAnalysis,
) -> None:
    answer = await _run(STATS_QUESTION, fake_analysis)

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
    partition of the rejects — a part with two defects is counted twice.
    """
    answer = await _run(STATS_QUESTION, fake_analysis)

    breakdown = next(f for f in answer.findings if "defect class" in f.statement)
    assert "0.5 or above" in breakdown.statement
    assert "counted twice" in breakdown.statement


async def test_a_breakdown_with_no_threshold_is_withheld_rather_than_stated() -> None:
    """The threshold is the semantics, not a decoration on it."""
    without = stats(total=600, rejects=30, gaps=[], rejects_without_class=12)
    del without["defect_class_threshold"]

    answer = await _run(STATS_QUESTION, FakeAnalysis({"inspection_stats": without}))

    assert not any("or above" in f.statement for f in answer.findings)
    assert any("did not say which score threshold" in c for c in answer.caveats)
    assert any("600 parts were inspected" in f.statement for f in answer.findings)


async def test_a_boolean_is_not_mistaken_for_a_threshold() -> None:
    """`bool` is a subclass of `int`, so the isinstance guard admits `True` unless it says
    otherwise — and "counting every class scoring True or above" is a sentence with no
    meaning."""
    lying = stats(total=600, rejects=30, gaps=[])
    lying["defect_class_threshold"] = True

    answer = await _run(STATS_QUESTION, FakeAnalysis({"inspection_stats": lying}))

    assert not any("True or above" in f.statement for f in answer.findings)
    assert any("did not say which score threshold" in c for c in answer.caveats)


async def test_rejects_no_class_explains_are_reported_rather_than_left_out() -> None:
    """§3.5 scenario 6 is a window of exactly these: every class present and every one of
    them decayed below the threshold."""
    answer = await _run(
        STATS_QUESTION,
        FakeAnalysis(
            {
                "inspection_stats": stats(
                    total=600, rejects=30, gaps=[], rejects_without_class=12
                )
            }
        ),
    )

    unaccounted = next(f for f in answer.findings if "no class scoring" in f.statement)
    assert "12 of those 30 rejects" in unaccounted.statement
    assert unaccounted.basis == "measured"


async def test_a_window_the_breakdown_fully_explains_says_nothing_extra(
    fake_analysis: FakeAnalysis,
) -> None:
    answer = await _run(STATS_QUESTION, fake_analysis)

    assert not any("no class scoring" in f.statement for f in answer.findings)


async def test_the_composer_summary_is_verified_like_any_other_claim() -> None:
    """§6.4: the summary "passes through verification like everything else". Its citations
    are inherited, so an id that does not resolve takes the summary with it."""
    fake = FakeAnalysis(
        {"inspection_stats": stats(total=600, rejects=30, gaps=[])}, known=set()
    )

    answer = await _run(STATS_QUESTION, fake)

    assert not any(
        finding.statement.startswith("In short:") for finding in answer.findings
    )
    assert any("could not be verified" in caveat for caveat in answer.caveats)


async def test_a_derived_finding_keeps_its_basis_through_composition() -> None:
    """§5.4's chain is a derivation, not a measurement, and the answer must not promote
    it."""
    fake = _stop_fake(stop("s-1", 660.0))

    answer = await _run(STOP_QUESTION, fake)

    assert any(finding.basis == "derived" for finding in answer.findings)


async def test_a_scripted_answer_says_it_was_not_produced_by_a_model(
    fake_analysis: FakeAnalysis,
) -> None:
    """The disclosure is in the prose a reader sees, not only in the trace."""
    answer = await _run(STATS_QUESTION, fake_analysis)

    assert answer.method.provider == "scripted"
    assert DISCLOSURE in answer.caveats
    assert "scripted provider" in answer.answer_markdown


async def test_the_pipeline_reports_each_step_before_the_answer(
    fake_analysis: FakeAnalysis,
) -> None:
    """§7.2: the reasoning is shown, not hidden behind a spinner."""
    items = [
        item
        async for item in stream(
            STATS_QUESTION,
            "s1",
            settings=Settings(),
            analysis=_client(fake_analysis),
            provider=ScriptedProvider(),
        )
    ]

    assert isinstance(items[-1], Answer), "the answer is not the last item"
    progress = [item.message for item in items if isinstance(item, Progress)]
    assert len(items) == len(progress) + 1, "more than one answer was streamed"
    assert any("classif" in line for line in progress)
    assert any("coverage" in line for line in progress)
    assert any("inspection_stats" in line for line in progress)
    assert any("citation" in line for line in progress)
