"""§6.1's staged pipeline, and the guards that make its answers trustworthy.

The stages are tested through the pipeline rather than one by one, because what each stage
must guarantee is a property of the answer: that the window came from the calendar and not
from the model, that coverage was consulted whether or not anything asked it to, that a
tool that failed reached the model instead of the user, and that nothing the reader sees is
a sentence the composer made up.

**What a green run here does and does not mean.** Every test below runs against
`ScriptedProvider`, which is keyword matching wearing the interface of a model. The stages
divide cleanly and the division is the honest headline of this file:

| stage | what a pass here proves |
|---|---|
| 2 window · 3 coverage · 4 routing · 6 verification · 6.5 composition | **proven without a model.** Code decides all of it; a model has no say and cannot change the outcome |
| 1 classification · 5 tool choice | **mechanism proven, judgement unproven.** That an unclassifiable question falls back with a caveat, that all fifteen operations are reachable, that a tool error returns to the model — yes. That a *model* would read this sentence as `quality_investigation`, or reach for `inspection_patterns` here — no test in this package can say |
| §6.7 asking · §6.5 contradiction | **mechanism proven, judgement unproven.** The rule fires in both directions given a classification, and a contradiction with no reasoning cannot ship. Whether a model would mark a question unranked, or disagree with a derivation that is wrong, is behavioural |

Every guarantee in this file is of the form *a badly formed answer cannot ship*. None is of
the form *a badly formed answer is not produced*, and M4 Task 8 must not read one as the
other when it writes the authenticity record.
"""

from __future__ import annotations

import logging

import pytest
from agent.answer import Answer, CitationWindow
from agent.classify import CLASSIFY_TOOL
from agent.config import Settings
from agent.pipeline import Progress, longest, run, stream
from agent.provider import ProviderReply, ToolCall
from agent.providers_scripted import DISCLOSURE, ScriptedProvider
from agent.records import Trace

from .fakes import (
    WINDOWS,
    FakeAnalysis,
    InventingProvider,
    as_client,
    coverage,
    line_status,
    pattern_report,
    stats,
    stop,
    stop_detail,
    stop_detail_intervention,
    stop_list,
)

STATS_QUESTION = "how many rejects in the last hour?"
STOP_QUESTION = "why did the line stand last night?"


async def _run(question: str, fake: FakeAnalysis, **kwargs: object) -> Answer:
    return await run(
        question,
        # `object` so a test can override any one setting by name; BaseSettings validates
        # each field, so a wrong type or an unknown name fails here rather than passing.
        settings=Settings(**kwargs),  # type: ignore[arg-type]  # BaseSettings validates
        analysis=as_client(fake),
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
#
# Proven without a model: the phrase is data the provider hands over and the window is the
# calendar's. A model that reads a phrase the calendar does not know lands on the caveat
# path, which is tested too.


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
#
# Proven without a model: nothing the model does decides whether this runs.


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


async def test_a_coverage_response_without_its_event_count_is_loud_not_empty() -> None:
    """The field is required by the contract, so its absence is a response that does not
    match its own shape. Defaulting it to zero would route to "I have no data for that
    window" — a confident wrong answer produced by a defence, which is the failure this
    system exists to refuse. Loud is the correct behaviour."""
    broken = coverage()
    del broken["observed"]

    with pytest.raises(RuntimeError, match="observed.events"):
        await _run(STATS_QUESTION, FakeAnalysis({"coverage": broken}))


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
#
# Mechanism proven, judgement unproven. That the fixed set constrains, that an unreadable
# question falls back with a caveat and that the type drives routing — yes. Whether a model
# would read *this* sentence as *this* type — no; the keyword table decides that here, and
# it was written to pass these tests.


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
#
# Mechanism proven, judgement unproven. The rule fires in both directions given a
# classification; whether a model would mark a question unranked is behavioural, and the
# disambiguator here is literally whether the word "scrap" appears.


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


async def test_the_reading_it_assumed_is_stated_and_so_is_the_one_it_passed_over(
    fake_analysis: FakeAnalysis,
) -> None:
    """§6.7 is one rule with two halves: "assume the most likely one **and say so in a
    caveat**". Not asking is only half of it — a reader who cannot see which reading was
    taken has no way to redirect an investigation that went the wrong way, and the answer
    looks exactly as confident either way."""
    answer = await _run("why are we losing parts to scrap?", fake_analysis)

    assumption = next(caveat for caveat in answer.caveats if "I read this as" in caveat)
    assert "scrapped" in assumption, "the reading it took is not named"
    assert "standing still" in assumption, "the reading it passed over is not named"


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
#
# Mechanism proven, judgement unproven. The dispatch, the error-to-model path, the transport
# catch, the consecutive-failure abort, the budget partial and the log are all proven. **Tool
# choice is not.** The scripted provider drives five of the fifteen operations end to end;
# the other ten are proven reachable in test_tools.py against a MockTransport, not chosen by
# anything.


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
    assert getattr(record, "tool") == "inspection_stats"  # noqa: B009 `extra` field
    assert getattr(record, "arguments") == {}  # noqa: B009 `extra` field
    assert getattr(record, "duration_ms") >= 0  # noqa: B009 `extra` field


async def test_the_tool_loop_can_reach_more_than_one_operation() -> None:
    """M1 called one hardcoded tool. A stop investigation now walks the list and then the
    detail, which is the shape SOP-01 describes."""
    fake = _stop_fake(stop("s-1", 660.0))

    answer = await _run(STOP_QUESTION, fake)

    assert answer.method.tools_called == ["list_stops", "get_stop"]


# --- stage 6.5: the composer, and what it is not allowed to do ------------------------
#
# Proven without a model: the composer is code, and what it receives is the shape a model
# would return. Whether a model's *findings* are worth arranging is not tested here.


async def test_budget_used_counts_loop_turns_and_not_tool_calls() -> None:
    """One field, one unit. §6.1 step 5 budgets *turns of the loop*, and `tools_called`
    already lists the calls — so `budget_used` carrying their count would be the same number
    twice in one object and a different number from the one the budget caps."""
    answer = await _run(STOP_QUESTION, _stop_fake(stop("s-1", 660.0)))

    assert answer.method.tools_called == ["list_stops", "get_stop"]
    assert answer.method.budget_used == 3, (
        "two calls took three turns: list, open, answer"
    )


async def test_a_removed_claim_is_logged_so_its_rate_can_be_measured(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§6.5: "the failure is logged so its frequency is measurable". The caveat makes one
    removal visible to one reader of one answer; the rate is what M7 scores citation
    integrity on, and a caveat cannot be counted across runs."""
    fake = FakeAnalysis(
        {"inspection_stats": stats(total=600, rejects=30, gaps=[])}, known=set()
    )

    with caplog.at_level(logging.WARNING, logger="agent.pipeline"):
        await _run(STATS_QUESTION, fake)

    record = next(
        r for r in caplog.records if r.msg == "citations removed from the answer"
    )
    assert getattr(record, "failed") == ["part:A-00000007"]  # noqa: B009 `extra` field
    assert getattr(record, "removed") == 1  # noqa: B009 `extra` field


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


async def test_a_stripped_claim_does_not_survive_in_the_prose() -> None:
    """§6.4: "every sentence the user reads traces to a verified finding."

    This is the test the reorder-verification-before-composition fix did not get, and its
    absence had the exact shape of the defect it fixed: reversing the two steps left the
    suite green while the removed claim went on standing in `answer_markdown` — gone from
    `findings`, still in the one field a reader actually reads. Asserting on `findings` and
    `caveats` alone cannot see that, because both are correct in the broken version.
    """
    fake = FakeAnalysis(
        {"inspection_stats": stats(total=600, rejects=30, gaps=[])}, known=set()
    )

    answer = await _run(STATS_QUESTION, fake)

    assert any("could not be verified" in caveat for caveat in answer.caveats), (
        "nothing was stripped, so this test proves nothing"
    )
    assert "600 parts were inspected" not in answer.answer_markdown


async def test_every_sentence_in_the_answer_traces_to_a_finding_it_ships() -> None:
    """The general form of the rule above, end to end rather than over the composer alone:
    every line of the prose is either a finding the answer carries or a caveat it states.
    Nothing else may reach the reader."""
    answer = await _run(STOP_QUESTION, _stop_fake(stop("s-1", 660.0)))

    statements = {finding.statement for finding in answer.findings}
    caveats = {f"_{caveat}_" for caveat in answer.caveats}
    for line in answer.answer_markdown.splitlines():
        stripped = line.removeprefix("- ").strip()
        if not stripped:
            continue
        assert stripped in statements or stripped in caveats, (
            f"prose the reader sees that is not a finding or a caveat: {stripped!r}"
        )


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
            settings=Settings(),
            analysis=as_client(fake_analysis),
            provider=ScriptedProvider(),
        )
    ]

    assert isinstance(items[-1], Trace), "the trace does not close the stream"
    assert isinstance(items[-2], Answer), "the answer does not precede the trace"
    progress = [item.message for item in items if isinstance(item, Progress)]
    assert len(items) == len(progress) + 2, "more than one answer was streamed"
    assert any("classif" in line for line in progress)
    assert any("coverage" in line for line in progress)
    assert any("inspection_stats" in line for line in progress)
    assert any("citation" in line for line in progress)


async def test_the_trace_records_what_the_run_actually_did(
    fake_analysis: FakeAnalysis,
) -> None:
    """§7.2 names what the trace holds: the SOPs loaded, every tool call with its arguments
    and timings, and the budget spent. Read off the same run the answer came from, because
    a trace that can disagree with the run it describes is worse than none."""
    items = [
        item
        async for item in stream(
            STATS_QUESTION,
            settings=Settings(),
            analysis=as_client(fake_analysis),
            provider=ScriptedProvider(),
        )
    ]
    answer = next(item for item in items if isinstance(item, Answer))
    trace = next(item for item in items if isinstance(item, Trace))

    assert trace.sops_loaded == answer.method.sops_used
    assert [call.name for call in trace.tool_calls] == answer.method.tools_called
    assert trace.budget.tool_turns == answer.method.budget_used
    assert trace.budget.tool_turns_limit == Settings().tool_budget
    assert all(call.duration_ms >= 0 for call in trace.tool_calls)
    assert trace.timings.total_ms >= trace.timings.tools_ms + trace.timings.model_ms


async def test_a_tool_failure_the_model_worked_around_is_in_the_trace() -> None:
    """§6.8 hands a failed call back to the model as a tool result, so the run recovers and
    the answer says nothing about it. The trace is where it stays visible — an audit trail
    that recorded only the calls that worked would describe a run that did not happen."""
    fake = FakeAnalysis(
        {"inspection_stats": stats(total=600, rejects=30, gaps=[])},
        flaky={"inspection_stats": "upstream timeout"},
    )

    items = [
        item
        async for item in stream(
            STATS_QUESTION,
            settings=Settings(),
            analysis=as_client(fake),
            provider=ScriptedProvider(),
        )
    ]
    trace = next(item for item in items if isinstance(item, Trace))

    assert trace.tool_calls[0].failed is True
    assert any(call.failed is False for call in trace.tool_calls)


# --- §6.5: contradiction, and the retry before a claim is removed ----------------------
#
# Mechanism proven, judgement unproven. A contradiction with no reasoning cannot be
# constructed, and DP-11's two computable checks fire in one direction and not in the other
# three. Whether a model would contradict a derivation that is wrong — or, worse, one that is
# right — is behavioural. Likewise the retry: this provider is deterministic and answers the
# same way twice, so the path is exercised and the claim is then removed; a model might fix
# it.

INTERVENTION_QUESTION = "why did the line stand last night at S3?"


def _intervention_fake(**changes: object) -> FakeAnalysis:
    detail = stop_detail_intervention("s-1")
    detail.update(changes)
    return FakeAnalysis(
        {"list_stops": stop_list(stop("s-1", 660.0)), "get_stop": detail}
    )


async def test_an_operator_intervention_mid_stop_is_contradicted_with_reasoning() -> (
    None
):
    """§8.1's contradiction class, and the case DP-11 was written for.

    The chain terminates at S3 in `held`; S3 raised no alarm of its own; S1 was already
    starved three minutes earlier. `Held` is a cause candidate *because* it requires an
    operator, which is the same property that makes "held because it faulted" and "held
    because somebody stopped it" identical to the computation — so the ordering is the only
    thing that can separate them, and the ordering is interpretation.

    Possible at all only because §5.4 returns the chain as **data rather than a verdict**:
    a bare "root: S3" could be disagreed with but not argued against.
    """
    fake = _intervention_fake()

    answer = await _run(INTERVENTION_QUESTION, fake)

    assert "DP-11" in answer.method.sops_used, "the pattern was not routed"
    assert answer.contradiction is not None
    assert answer.contradiction.derived_root == "S3"
    assert answer.contradiction.agent_root == "S1"
    assert "13:31" in answer.contradiction.reasoning
    assert "13:34" in answer.contradiction.reasoning


async def test_the_contradiction_travels_as_a_hypothesis_with_its_evidence() -> None:
    """DP-11: "The contradiction is a `hypothesis`, and its evidence strength is that
    sequence." It is an interpretation of the chain, not a reading of it."""
    answer = await _run(INTERVENTION_QUESTION, _intervention_fake())

    hypothesis = next(f for f in answer.findings if f.basis == "hypothesis")
    assert hypothesis.evidence_strength
    assert "13:31" in hypothesis.evidence_strength
    assert {citation.kind for citation in hypothesis.citations} == {"stop", "sop"}


async def test_a_root_carrying_its_own_alarm_is_not_contradicted() -> None:
    """DP-11's own refutation, and the mirror this class needs: "The station carrying its
    own alarm, raised before any disturbance elsewhere. That is a genuine cause candidate
    and the computation is right." Contradicting here would be the failure."""
    detail = stop_detail_intervention("s-1")
    alarms = detail["alarms"]
    assert isinstance(alarms, list)
    fake = _intervention_fake(alarms=[{**alarms[0], "station": "S3"}])

    answer = await _run(INTERVENTION_QUESTION, fake)

    assert answer.contradiction is None
    assert not any(f.basis == "hypothesis" for f in answer.findings)


async def test_a_disturbance_that_only_follows_the_intervention_does_not_contradict() -> (
    None
):
    """The ordering **is** the argument, and this is the direction that proves it is being
    read. DP-11's first refutation: "The intervention starting after the chain's root
    episode. Then the chain is following the fault and reached the intervention on its way;
    nothing is distorted."

    Everything else about this stop is unchanged — S3 still `held`, still with no alarm of
    its own. Only S1's disturbance has moved to *after* S3 was held, and a cause that
    arrives second is not a cause.
    """
    detail = stop_detail_intervention("s-1")
    timeline = detail["timeline"]
    assert isinstance(timeline, list)
    fake = _intervention_fake(
        timeline=[
            {**timeline[0], "from_ts": "2026-09-12T13:40:00Z"},
            timeline[1],
        ]
    )

    answer = await _run(INTERVENTION_QUESTION, fake)

    assert answer.contradiction is None


async def test_a_chain_with_no_intervention_in_it_is_not_contradicted() -> None:
    """DP-11 again: do not reach for this pattern to explain a chain you merely find
    surprising."""
    fake = _stop_fake(stop("s-1", 660.0))

    answer = await _run(STOP_QUESTION, fake)

    assert answer.contradiction is None


async def test_the_agent_cites_only_procedures_it_was_given() -> None:
    """§6.5: "the model cannot invent a procedure it never read". The provider reads the
    routed documents out of its own system prompt, the way a model would; what it cites is
    then a subset of what routing loaded by construction, and verification enforces it."""
    answer = await _run(STOP_QUESTION, _stop_fake(stop("s-1", 660.0)))

    cited = {
        citation.id
        for finding in answer.findings
        for citation in finding.citations
        if citation.kind == "sop"
    }

    assert cited
    assert cited <= set(answer.method.sops_used)


async def test_an_unverifiable_citation_gets_one_retry_before_the_claim_goes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§6.5 gives exactly one retry, then removes the claim and says so — and logs the
    failure, so its frequency is measurable rather than anecdotal.

    Whether a *model* would take the correction is behavioural: this provider is
    deterministic and answers the same way twice, which is why the claim is removed here.
    """
    fake = FakeAnalysis({"inspection_stats": stats(600, 30, [])}, known=set())

    with caplog.at_level(logging.WARNING, logger="agent.pipeline"):
        answer = await _run(STATS_QUESTION, fake)

    assert any("could not be verified" in caveat for caveat in answer.caveats)
    assert any(
        record.msg == "citations failed verification" for record in caplog.records
    )
    assert [name for name, _ in fake.calls].count("resolve:get_part") == 2, (
        "the model was not asked a second time"
    )


# --- §7.3: the window a citation carries -----------------------------------------------
#
# Proven without a model. The interval is the calendar's (stage 2), and the citations the
# model hands back are rewritten from it before anything is verified or delivered — so what
# is asserted here is not that a model behaves, but that it cannot reach the field at all.


def _windowed_final(window: dict[str, str] | None) -> dict[str, object]:
    """One finding citing a pattern, a signal and a part, with `window` as the model left it.

    Three kinds on one finding rather than three findings, so the two that take a window and
    the one that does not are decided by the same pass over the same claim.
    """
    pattern: dict[str, object] = {"kind": "pattern", "dimension": "carrier", "key": "7"}
    part: dict[str, object] = {"kind": "part", "id": "A-00000007"}
    if window is not None:
        pattern["window"] = window
        part["window"] = window
    return {
        "findings": [
            {
                "statement": "Carrier 7 is over-represented among the rejects.",
                "basis": "measured",
                "citations": [
                    pattern,
                    {"kind": "signal", "station": "S2", "signal": "JoiningForce"},
                    part,
                ],
            }
        ],
        "caveats": [],
    }


async def _windowed_answer(question: str, window: dict[str, str] | None) -> Answer:
    fake = FakeAnalysis({"inspection_patterns": pattern_report("carrier", "7")})
    return await run(
        question,
        settings=Settings(),
        analysis=as_client(fake),
        provider=InventingProvider(_windowed_final(window)),
    )


def _cited_windows(answer: Answer) -> dict[str, CitationWindow | None]:
    return {
        citation.kind: citation.window
        for finding in answer.findings
        for citation in finding.citations
    }


@pytest.mark.parametrize(
    ("question", "phrase"),
    [(STOP_QUESTION, "last night"), (STATS_QUESTION, "last hour")],
)
async def test_a_windowed_citation_carries_the_window_the_question_resolved_to(
    question: str, phrase: str
) -> None:
    """§7.3 writes a `window` into the `pattern` and `signal` citations, and this is the
    window the answer was computed over — not a recent default the reader's screen picked.

    Two questions rather than one, because a citation that always carried the same interval
    would satisfy a single case and be a constant rather than a window.
    """
    expected = WINDOWS[phrase]

    answer = await _windowed_answer(question, None)

    for kind in ("pattern", "signal"):
        carried = _cited_windows(answer)[kind]
        assert carried is not None, f"the {kind} citation shipped without a window"
        assert carried.from_ts == expected.start
        assert carried.to_ts == expected.end
        assert carried.label == expected.label


async def test_a_window_the_model_typed_does_not_reach_the_reader() -> None:
    """The reason the field is written here and not by the model: a window it could type
    into a citation is data drawn onto the panel that citation opens (§7.4), and a panel
    showing real rows under a sentence that was never about them reads authoritatively."""
    invented = {
        "from_ts": "2026-03-01T00:00:00Z",
        "to_ts": "2026-03-01T01:00:00Z",
        "label": "an interval the model typed",
    }

    answer = await _windowed_answer(STOP_QUESTION, invented)

    carried = _cited_windows(answer)["pattern"]
    assert carried is not None
    assert carried.label == WINDOWS["last night"].label
    assert "an interval the model typed" not in answer.model_dump_json()


async def test_a_kind_that_takes_no_window_ships_without_one() -> None:
    """§7.3 gives a window to the two kinds whose endpoints need one. `GET /parts/{serial}`
    takes none, and a window beside a citation that does not use it is an interval a reader
    would take for a qualification on the claim."""
    answer = await _windowed_answer(
        STOP_QUESTION,
        {
            "from_ts": "2026-03-01T00:00:00Z",
            "to_ts": "2026-03-01T01:00:00Z",
            "label": "an interval the model typed",
        },
    )

    assert _cited_windows(answer)["part"] is None


# --- §7.4: a chart is a citation, and its data is a tool call this run made -------------
#
# Proven without a model in the half that matters: whether a *model* would pick a fitting
# chart type is §8.1's statistics-and-charts class. What is proven here is that a chart
# naming a call the run never made, or one that failed, cannot reach the reader — and that
# the interval its axes are labelled with is the calendar's.


class ChartingProvider:
    """A model that calls one tool and then cites a chart against it.

    `InventingProvider` finals immediately and so makes no calls, which is exactly the
    situation a chart must not be drawn in — so this one is needed to show the other side:
    a chart whose `source` is a call that really happened.
    """

    name = "charting"

    def __init__(self, source: str) -> None:
        self._source = source
        self._called = False

    async def call(
        self,
        system: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ProviderReply:
        if [str(tool.get("name")) for tool in tools] == [CLASSIFY_TOOL["name"]]:
            return await ScriptedProvider().call(system, messages, tools)
        if not self._called:
            self._called = True
            return ProviderReply(
                tool_calls=[
                    ToolCall(
                        id="call_inspection_stats",
                        name="inspection_stats",
                        arguments={"group_by": "defect_class"},
                    )
                ]
            )
        return ProviderReply(
            final={
                "findings": [
                    {
                        "statement": "Misalignment is the largest class.",
                        "basis": "measured",
                        "citations": [
                            {
                                "kind": "chart",
                                "chart_type": "pareto",
                                "source": self._source,
                                "options": {
                                    "series": "defect_classes",
                                    "x": "defect_class",
                                    "y": "count",
                                },
                                # A window the model typed, to be overwritten like any other.
                                "window": {
                                    "from_ts": "2026-03-01T00:00:00Z",
                                    "to_ts": "2026-03-01T01:00:00Z",
                                    "label": "an interval the model typed",
                                },
                            }
                        ],
                    }
                ],
                "caveats": [],
            }
        )


async def _charted(source: str) -> Answer:
    fake = FakeAnalysis({"inspection_stats": stats(600, 30, [])})
    return await run(
        STATS_QUESTION,
        settings=Settings(),
        analysis=as_client(fake),
        provider=ChartingProvider(source),
    )


async def test_a_chart_citing_a_call_this_run_made_reaches_the_reader() -> None:
    """The other side of every refusal below: a guard that removed all of them would pass
    those tests while making the feature unreachable."""
    answer = await _charted("call_inspection_stats")

    charts = [
        citation
        for finding in answer.findings
        for citation in finding.citations
        if citation.kind == "chart"
    ]
    assert len(charts) == 1
    assert charts[0].source == "call_inspection_stats"


async def test_a_chart_citing_a_call_that_never_happened_takes_its_claim_with_it() -> (
    None
):
    """§7.4's reason, applied: a chart drawn from nothing reads far more authoritatively
    than a wrong sentence, so the claim goes and the answer says it went."""
    answer = await _charted("call_that_never_happened")

    assert answer.findings == []
    assert any("could not be verified" in caveat for caveat in answer.caveats)


async def test_a_charts_axes_are_labelled_with_the_calendars_window() -> None:
    """The same rule §7.3 applies to a pattern cell, for the same reason. Every tool result
    a chart reads was computed over this run's window; an axis labelled with any other
    interval is §7.4's failure moved from the values to the ruler."""
    answer = await _charted("call_inspection_stats")

    carried = answer.findings[0].citations[0].window
    assert carried is not None
    assert carried.label == WINDOWS["last hour"].label
    assert "an interval the model typed" not in answer.model_dump_json()


async def test_the_scripted_provider_charts_the_call_it_really_made() -> None:
    """§7.4's renderer would otherwise ship exercised only by its own tests.

    `ScriptedProvider` is the default and needs no credentials, so what it cites is what a
    keyless run — and the demo — actually shows. Its Pareto names the `inspection_stats`
    call it made moments earlier, which is the whole of §7.4 in the one configuration
    anybody can run: the chart references a verified tool result and carries no figure.

    That it *survives* is the assertion. §6.5 would have removed it had the id been one the
    run never made, so a green here is the verification agreeing with the citation.
    """
    fake = FakeAnalysis({"inspection_stats": stats(600, 30, [])})

    answer = await _run(STATS_QUESTION, fake)

    charts = [
        citation
        for finding in answer.findings
        for citation in finding.citations
        if citation.kind == "chart"
    ]
    # One chart, cited twice: §6.5's composer gives its summary the citations of the
    # findings it summarises, so the same referent appears under both.
    assert {(chart.chart_type, chart.source) for chart in charts} == {
        ("pareto", "call_inspection_stats")
    }
    first = charts[0]
    assert first.options is not None
    assert first.options.series == "by_defect_class"
    # And the window it is labelled with is the calendar's, not one this provider typed.
    assert first.window is not None
    assert first.window.label == WINDOWS["last hour"].label
