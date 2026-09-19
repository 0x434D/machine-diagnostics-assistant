"""§6.5: every cited id is resolved against the database before the answer ships.

A citation that cannot be opened is, in the spec's words, barely a citation — so an id that
does not resolve is removed rather than shipped, and the answer says that it was.

**Every kind in §7.3's vocabulary resolves against a real endpoint**, and `resolves` below
is the whole map from kind to endpoint. A kind whose referent had no endpoint would not be a kind;
two in this vocabulary have no endpoint *of their own* and are resolved by membership in the
window's own answer instead, which is stated where it happens.

**Two kinds carry the window their claim was made over** (§7.3), and `stamped` writes it from
the window the run resolved rather than letting the model name one. It runs before the
resolution below, so the interval a `pattern` or `signal` citation ships with is the interval
it was checked against — the window is part of what makes those two verifiable, not an
exception to it.

**A cited procedure is checked twice.** That it exists, against `GET /knowledge/{id}`, and
that routing actually loaded it — §6.5: *"so the model cannot invent a procedure it never
read."* Those are different failures. A document that exists but was not routed is a
procedure the model is claiming to have followed and did not see, which reads exactly like
a correct citation and is not one.

What this does **not** do is judge whether the citation supports the claim. §8.1 scores that
against ground truth, with something that can read both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agent.answer import WINDOWED, Citation, CitationWindow, Finding
from agent.tools import AnalysisClient, Window


@dataclass(frozen=True)
class Verification:
    """What survived §6.5, what did not, and the sentence that says so.

    `failed` is carried beside `note` because the two have different readers: the note is a
    caveat a person reads, and the labels are what goes back to the model for its one retry
    and what a trace counts. Deriving either from the other would mean parsing prose.
    """

    kept: list[Finding] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    #: How many findings went, which is not how many citations failed — one claim can rest
    #: on several, and a reader counting sentences needs the first number.
    removed: int = 0

    @property
    def note(self) -> str | None:
        """The caveat, or `None` when nothing was removed."""
        if not self.failed:
            return None
        return (
            f"{self.removed} claim(s) were removed because their citations could not be "
            f"verified against the database: {', '.join(self.failed)}."
        )


def stamped(findings: Sequence[Finding], window: Window) -> list[Finding]:
    """§7.3's `window`, written onto the citations that take one from the run's own window.

    The window is the calendar's (§6.1 step 2) and never the model's: whatever a model put
    there is overwritten, and the kinds that take no window have theirs cleared. That is the
    whole point of doing it here — a window a model could type into a citation is data drawn
    onto the panel that citation opens, which is §7.4's failure moved from a chart into a
    table, where it reads just as authoritatively.

    Run **before** `keep`, so the interval a citation carries is the interval it was verified
    over: `resolves` checks a pattern cell and a signal series against this same window, and
    a citation stamped afterwards could name one the verification never looked at.
    """
    carried = CitationWindow(from_ts=window.start, to_ts=window.end, label=window.label)
    return [
        finding.model_copy(
            update={
                "citations": [
                    # `model_copy` rather than rebuilding through the validator: the payload
                    # check has already run on this citation and `window` is not part of
                    # what it looks at, so re-validating would only repeat a check that
                    # passed.
                    citation.model_copy(
                        update={
                            "window": carried if citation.kind in WINDOWED else None
                        }
                    )
                    for citation in finding.citations
                ]
            }
        )
        for finding in findings
    ]


async def keep(
    findings: Sequence[Finding],
    analysis: AnalysisClient,
    *,
    window: Window,
    loaded: frozenset[str],
) -> Verification:
    """The findings whose citations all resolve, and what was taken out.

    §6.5 gives the model one retry after this runs; `pipeline.stream` performs it, and
    `test_an_unverifiable_citation_gets_one_retry_before_the_claim_goes` is what fails if it
    stops. What this must never do is ship the claim quietly: the removal is visible in the
    answer, logged so its rate is measurable, and countable in the trace.

    Findings rather than an assembled answer, because §6.1 composes *after* this: a claim
    stripped from `findings` but left standing in `answer_markdown` is the removal made
    invisible again, in the one field the reader actually reads. That ordering is the whole
    point of taking findings here, and `test_a_stripped_claim_does_not_survive_in_the_prose`
    is what fails if it is reversed — it went unenforced once, with the docstring already
    written.

    A finding is dropped when one of **its own** citations fails. Matching by label would
    have been shorter and is wrong: two containment scopes are two different referents and
    can share a label, so one failing would silently take the other's claim with it.

    `loaded` is what routing selected, not what the knowledge base holds.
    """
    reports: dict[str, dict[str, object]] = {}
    decided: dict[str, bool] = {}
    kept: list[Finding] = []
    failed: list[str] = []

    for finding in findings:
        survives = True
        for citation in finding.citations:
            # Keyed on the whole citation, so the same referent is resolved once per pass
            # and two citations that merely render alike are still two questions.
            key = citation.model_dump_json()
            if key not in decided:
                decided[key] = await resolves(
                    citation, analysis, window=window, loaded=loaded, cache=reports
                )
            if not decided[key]:
                survives = False
                if citation.label not in failed:
                    failed.append(citation.label)
        if survives:
            kept.append(finding)

    return Verification(
        kept=kept, failed=sorted(failed), removed=len(findings) - len(kept)
    )


async def resolves(
    citation: Citation,
    analysis: AnalysisClient,
    *,
    window: Window,
    loaded: frozenset[str],
    cache: dict[str, dict[str, object]] | None = None,
) -> bool:
    """Whether this citation opens onto something that exists.

    Dispatch is exhaustive over §7.3's vocabulary; `Kind` and `CITATION_FIELDS` in
    `answer.py` are the other two places the same nine live, and the validator there
    guarantees the payload each branch reads is present.
    """
    memo = {} if cache is None else cache
    kind = citation.kind

    if kind == "part":
        return await analysis.exists("get_part", {"serial": citation.id}, window)
    if kind == "serial":
        # A *component* serial, which is a different referent from an assembly serial and
        # has its own endpoint. §7.3's example maps `serial` to /parts/, i.e. to the same
        # place `part` goes — see the report: read that way the two kinds are one kind
        # spelled twice, and this reading is the one that gives each a referent.
        return await analysis.exists(
            "component_assembly", {"serial": citation.id}, window
        )
    if kind == "stop":
        return await analysis.exists("get_stop", {"identifier": citation.id}, window)
    if kind == "lot":
        return await analysis.exists("lot_parts", {"lot_code": citation.id}, window)
    if kind == "signal":
        # Resolves the *station*, and cannot resolve the signal name: §5.3 answers an
        # unknown name with an empty series rather than a 404, so "S2 publishes no
        # JoiningForce" and "S2's JoiningForce was silent this hour" arrive identically.
        # Treating an empty series as a failed citation would strip the true claim that a
        # signal went quiet, which is a finding in its own right. Named in the report as a
        # hole this vocabulary cannot close without an endpoint that lists a station's
        # signals.
        return await analysis.exists(
            "signal_trend",
            {"station": citation.station, "signal": citation.signal},
            window,
        )
    if kind == "sop":
        return citation.id in loaded and await analysis.knowledge_exists(
            str(citation.id)
        )
    if kind == "alarm":
        return _alarm_in(
            await _once(memo, "list_alarms", analysis, window), str(citation.id)
        )
    if kind == "pattern":
        return _pattern_in(
            await _once(memo, "inspection_patterns", analysis, window),
            str(citation.dimension),
            str(citation.key),
        )
    # containment: the scope is a list of serials, and every one of them must be a real
    # part. Whether it is the *right* scope — no misses, no false inclusions — is §8.1's
    # containment class, scored against ground truth by the harness rather than here.
    for serial in citation.serials or []:
        if not await analysis.exists("get_part", {"serial": serial}, window):
            return False
    return True


async def _once(
    memo: dict[str, dict[str, object]],
    name: str,
    analysis: AnalysisClient,
    window: Window,
) -> dict[str, object]:
    """One fetch per window-scoped report per verification pass.

    An answer citing eight alarms would otherwise read the same window eight times, and the
    second read could disagree with the first while the answer was being checked.
    """
    if name not in memo:
        memo[name] = await analysis.fetch(name, {}, window)
    return memo[name]


def _alarm_in(report: Mapping[str, object], identifier: str) -> bool:
    alarms = report.get("alarms")
    if not isinstance(alarms, list):
        return False
    return any(
        isinstance(alarm, dict) and str(alarm.get("id")) == identifier
        for alarm in alarms
    )


def _pattern_in(report: Mapping[str, object], dimension: str, key: str) -> bool:
    """A pattern cell exists when the dimension was computed and holds that value.

    Not "and was significant": a citation supporting *"there is no pattern on carrier 3"*
    points at the cell that says so, and §8.1's negative class is about exactly that answer.
    """
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, list):
        return False
    for section in dimensions:
        if not isinstance(section, dict) or section.get("dimension") != dimension:
            continue
        values = section.get("patterns")
        if not isinstance(values, list):
            continue
        if any(
            isinstance(value, dict) and str(value.get("value")) == key
            for value in values
        ):
            return True
    return False
