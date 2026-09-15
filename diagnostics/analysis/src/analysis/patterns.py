"""§5.5 over one dimension: every value against the rest, and usually nothing.

Each value of the chosen dimension is compared against the pool with that value taken
out — the leave-one-out shape M2c's own measurement uses, and the only honest reference
when the thing under test is part of the pool. A dimension with a single value therefore
has no reference at all, and says so rather than dividing by zero.

**A comparison can be stratified, and §3.5 scenario 4 is the row that requires it.** Asked
`within` a second dimension, every value is compared against the rest of the pool inside one
value of that second dimension — carrier 7's `misalignment` rate against the other carriers'
`misalignment` rate, rather than carrier 7's whole reject rate against theirs. §5.5 lists
four dimensions and none of them is a pair; §3.5 row 4 is a *class concentrated on a
carrier*, and `find_patterns` carries the measurement of what testing the carrier alone
costs.

**Multiple comparisons: Benjamini-Hochberg, at the same α.** Twelve carriers tested at
α=0.05 produce a finding on a line where nothing is wrong more often than not — measured
here at 54 runs in 150 of twelve identical carriers, against the 4 the correction leaves.
Without it, §5.5's whole purpose is lost at the first `GROUP BY` with more than a few
rows in it.

What the choice costs, stated because it is a real difference and not a free lunch:

- Benjamini-Hochberg controls the **false discovery rate** — the expected share of the
  findings reported that are not real — where Bonferroni controls the probability of
  **any** false finding at all. On a clean line the two are the same thing: every
  rejection would be a false one, so the FDR *is* the chance of any finding, and BH is
  exactly as strict as Bonferroni. That is the case §5.5 cares about most and it is not
  where they differ.
- They differ once something real is there. Bonferroni's flat α/m spends the whole
  budget on the first finding and loses the second and third; BH's step-up keeps them
  (`test_benjamini_hochberg_finds_a_third_effect_bonferroni_misses`: three worn carriers,
  Bonferroni reaches two). A line with two bad carriers is not a rare shape.
- The price is what a report of several findings means. BH says up to α of *those
  findings* may be false; Bonferroni says each one individually is safe at α. A report
  of four carriers may carry one that is not there, and the agent must not present the
  fourth as certainly as the first.

Both, and no correction at all, are configuration (§10.3) — `Correction.NONE` is what
the corrected rates in the tests are measured against.

**Only comparisons that cleared the sample gate are in the family.** A value with too
few parts carries no p-value to correct, and counting it in `m` would spend power to
correct for a test that was never run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import assert_never

from analysis.significance import (
    DEFAULT_SIGNIFICANCE,
    Comparison,
    Counts,
    SignificanceSettings,
    Verdict,
    compare_proportions,
)


class Dimension(Enum):
    """§5.5's four names, and the one §3.5 scenario 7 cannot be answered without.

    Each is one field of an `Observation`.

    **`LOT` is not in §5.5's list and has to be.** Scenario 7 is a run of rising `gap`
    defects where the joining force is *perfectly stable*: the symptom points straight at a
    press drift, and the only thing separating that wrong answer from the right one is that
    the defects correlate with the supplier lot rather than with the force. Without a lot
    dimension there is nothing for that correlation to be measured in, and the scenario's
    whole proof has nothing to stand on.

    It is a legitimate dimension and not a special case: a part's lot membership is a
    per-part fact, reached through `genealogy` to `components.lot_id`, and never a time
    join — which is exactly what §3.5's staggered lot boundaries exist to make checkable.
    """

    CARRIER = "carrier"
    LANE = "lane"
    LOT = "lot"
    DEFECT_CLASS = "defect_class"
    TIME_BUCKET = "time_bucket"


@dataclass(frozen=True, kw_only=True)
class Observation:
    """One trial: where it sat along each dimension, and whether the thing counted
    happened.

    What a trial is and what `outcome` counts are the caller's to decide — an inspected
    part that was rejected, a reject that carried one defect class — and the denominator
    follows from that choice. A dimension the caller did not supply is None, and an
    observation is left out of the comparison along any dimension it has no value for:
    a part whose carrier the gateway never saw belongs to no carrier and cannot be
    compared to one. Those are counted, in `PatternReport.unattributed`, rather than
    quietly dropped.
    """

    outcome: bool
    carrier: str | None = None
    lane: str | None = None
    lot: str | None = None
    defect_class: str | None = None
    time_bucket: str | None = None

    def value(self, dimension: Dimension) -> str | None:
        match dimension:
            case Dimension.CARRIER:
                return self.carrier
            case Dimension.LANE:
                return self.lane
            case Dimension.LOT:
                return self.lot
            case Dimension.DEFECT_CLASS:
                return self.defect_class
            case Dimension.TIME_BUCKET:
                return self.time_bucket
        assert_never(dimension)


class Correction(Enum):
    """NONE is not a default anyone should deploy; it is what the other two are measured
    against, and what a caller testing a single value by hand does not need."""

    NONE = "none"
    BONFERRONI = "bonferroni"
    BENJAMINI_HOCHBERG = "benjamini_hochberg"


@dataclass(frozen=True)
class PatternSettings:
    significance: SignificanceSettings = DEFAULT_SIGNIFICANCE
    correction: Correction = Correction.BENJAMINI_HOCHBERG


@dataclass(frozen=True)
class Pattern:
    """One value of the dimension, and what can be said about it.

    `verdict` is the answer — after correction. `comparison.verdict` is the same question
    asked of this value alone, before the other eleven were taken into account, and is
    kept because the raw p-value is the number a reader checks the correction against.
    `verdict` is never stronger than `comparison.verdict`.

    `adjusted_p_value` is None exactly when the value did not clear the sample gate.

    `stratum` is the value of the dimension this comparison was made *inside*, and is None
    unless the report was asked for `within` one — carrier 7 measured on `misalignment`
    alone rather than on everything it scrapped.
    """

    value: str
    comparison: Comparison
    adjusted_p_value: float | None
    verdict: Verdict
    stratum: str | None = None


@dataclass(frozen=True)
class PatternReport:
    """The answer to one `/inspection/patterns` question.

    `patterns` carries every value, including the ones with nothing to report, because
    "carrier 7 is 4 % above average, n=38, not significant" is an answer §5.5 asks for
    and an empty list would not carry it. It is ordered by adjusted p-value, the values
    that could not be tested last, ties by value then stratum — so it does not depend on
    the order the observations arrived in, which SQL does not promise.

    `within` is the dimension the comparisons were stratified by, or None. It is part of
    the report's identity and not a note about it: "carrier" and "carrier within defect
    class" are two different questions with two different answers, and a reader handed the
    second labelled as the first would read a class-scoped finding as a line-wide one.
    """

    dimension: Dimension
    patterns: tuple[Pattern, ...]
    unattributed: int
    within: Dimension | None = None

    @property
    def significant(self) -> tuple[Pattern, ...]:
        """The findings, strongest first — and empty is the expected answer."""
        return tuple(
            pattern
            for pattern in self.patterns
            if pattern.verdict is Verdict.SIGNIFICANT
        )


DEFAULT_PATTERNS = PatternSettings()
"""One shared instance of the defaults, frozen like everything it holds."""


Key = tuple[str, str | None]
"""One comparison's identity: the value tested, and the stratum it was tested inside."""


def find_patterns(
    observations: Sequence[Observation],
    dimension: Dimension,
    settings: PatternSettings = DEFAULT_PATTERNS,
    *,
    within: Dimension | None = None,
) -> PatternReport:
    """Test every value of `dimension` against the rest of the pool.

    Returning a report in which nothing is significant is the ordinary outcome on a
    healthy line and is not an error.

    **`within` stratifies the comparison, and §3.5 scenario 4 is why it exists.** That row
    is a *class concentrated on a carrier* — `misalignment` and `scratch` on carrier 7 —
    and the carrier dimension on its own tests each carrier's whole reject rate, which
    dilutes two of six classes into all six. Measured on the shipped magnitudes, the
    dilution is decisive: a worn carrier at the pool's median is invisible to the plain
    dimension at every depth, and reachable within the strata at 1,200 parts per carrier
    (`test_analysis_proof`, and the numbers are in `measurements/authenticity/README.md`).

    So each value is compared against the rest of the pool **inside one value of `within`**:
    carrier 7's `misalignment` rate against every other carrier's `misalignment` rate, never
    against the pooled rate of six classes with different base rates.

    **The multiplicity family is every stratum's comparisons together**, because "does any
    carrier concentrate any class" is one question asked of 18 × 6 hypotheses. Correcting
    each class separately would under-correct by the number of strata, which is the failure
    this module's docstring measures for the uncorrected case.

    An observation carrying no value along `dimension` *or* along `within` is unattributed:
    it belongs to no comparison, and a part with no carrier cannot be compared to one
    whichever class is being looked at.
    """
    strata: dict[str | None, list[Observation]] = {}
    unattributed = 0
    for observation in observations:
        stratum = None if within is None else observation.value(within)
        if observation.value(dimension) is None or (
            within is not None and stratum is None
        ):
            unattributed += 1
            continue
        strata.setdefault(stratum, []).append(observation)

    comparisons: dict[Key, Comparison] = {}
    for stratum, members in strata.items():
        for value, comparison in _compare(members, dimension, settings).items():
            comparisons[(value, stratum)] = comparison

    tested = {
        key: comparison.p_value
        for key, comparison in comparisons.items()
        if comparison.p_value is not None
    }
    adjusted = _adjust(tested, settings.correction)

    patterns = [
        Pattern(
            value=key[0],
            stratum=key[1],
            comparison=comparison,
            adjusted_p_value=adjusted.get(key),
            verdict=_verdict(comparison, adjusted.get(key), settings),
        )
        for key, comparison in comparisons.items()
    ]
    patterns.sort(
        key=lambda pattern: (
            pattern.adjusted_p_value is None,
            pattern.adjusted_p_value or 0.0,
            pattern.value,
            pattern.stratum or "",
        )
    )
    return PatternReport(
        dimension=dimension,
        patterns=tuple(patterns),
        unattributed=unattributed,
        within=within,
    )


def _compare(
    observations: Sequence[Observation], dimension: Dimension, settings: PatternSettings
) -> dict[str, Comparison]:
    """Every value of `dimension` against the pool with that value taken out.

    Leave-one-out, which is the only honest reference when the thing under test is part of
    the pool — and the same statistic `test_noise` and `test_scenario_consequences` report
    their *d* in, so the numbers on either side of the boundary compare.
    """
    successes: Counter[str] = Counter()
    trials: Counter[str] = Counter()
    for observation in observations:
        value = observation.value(dimension)
        if value is None:
            continue
        trials[value] += 1
        if observation.outcome:
            successes[value] += 1

    all_successes = sum(successes.values())
    all_trials = sum(trials.values())
    return {
        value: compare_proportions(
            Counts(successes[value], count),
            Counts(all_successes - successes[value], all_trials - count),
            settings.significance,
        )
        for value, count in trials.items()
    }


def _verdict(
    comparison: Comparison, adjusted_p_value: float | None, settings: PatternSettings
) -> Verdict:
    if adjusted_p_value is None:
        return comparison.verdict  # NOT_ENOUGH_DATA: the gate, not the correction
    if adjusted_p_value <= settings.significance.alpha:
        return Verdict.SIGNIFICANT
    return Verdict.NOT_SIGNIFICANT


def _adjust(
    p_values: dict[Key, float], correction: Correction
) -> dict[Key, float | None]:
    """The p-value each test must beat α with, once the others are accounted for.

    Adjusting the p-values rather than lowering α keeps one comparison against one α
    everywhere, and leaves the adjusted number readable next to the raw one.
    """
    comparisons = len(p_values)
    match correction:
        case Correction.NONE:
            return {key: p for key, p in p_values.items()}
        case Correction.BONFERRONI:
            return {key: min(1.0, p * comparisons) for key, p in p_values.items()}
        case Correction.BENJAMINI_HOCHBERG:
            return _benjamini_hochberg(p_values)
    assert_never(correction)


def _benjamini_hochberg(p_values: dict[Key, float]) -> dict[Key, float | None]:
    """Step-up: p × m / rank, walked from the weakest test to the strongest and held
    monotone, so a test is never adjusted past one that was less convincing than it."""
    ranked = sorted(
        p_values.items(), key=lambda item: (item[1], item[0][0], item[0][1] or "")
    )
    comparisons = len(ranked)
    adjusted: dict[Key, float | None] = {}
    running = 1.0
    for rank, (key, p_value) in reversed(list(enumerate(ranked, start=1))):
        running = min(running, p_value * comparisons / rank)
        adjusted[key] = running
    return adjusted
