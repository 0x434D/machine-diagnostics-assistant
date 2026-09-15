"""Two proportions, compared exactly, and the three answers §5.5 needs.

Pure arithmetic — no parts, no carriers, no window. `patterns` supplies the meaning and
`routes_inspection` the data.

**The test is Fisher's exact test, and §15's open decision closes here.** The choice was
deferred to M3 because it "needs the noise floor's real distribution"; M2c shipped one,
and it is what rules the two-proportion z out. Measured over twelve carriers drawn from
`simulator.noise`'s lognormal spread (σ=0.35, normalised across the pool) at §3.5's
1.5 % reject rate, 100 parts each, Benjamini-Hochberg at α=0.05, the same 300 clean runs
put through both tests:

| clean runs reporting a carrier | exact | normal z |
|---|---|---|
| drawn from the real noise floor | 10 / 300 | 57 / 300 |
| twelve identical carriers (α is the truth) | 5 / 300 | 23 / 300 |

The reason is the line's rate. At 1.5 % the observed cell holds single-digit counts, the
normal approximation wants an expected count around five before it is worth anything
(n ≈ 333 per carrier here), and where it is wrong it is wrong in the direction of
claiming too much. A z would have handed the agent a story out of the permanent noise
floor in one clean run in five, which is the exact failure §5.5 exists to prevent.

Two further notes on the choice, both deliberate:

- **Two-sample exact, not a one-sample binomial against a known p.** The expected share
  is estimated from the same window — it is the rest of the pool — and treating it as
  given would understate the uncertainty in the same anti-conservative direction as z.
- **Fisher conditions on the margins and is therefore discrete and conservative**: its
  real rejection rate sits under the nominal α (the 5 / 300 above, against the 15 / 300
  α promises). It errs toward "not significant", which is the direction this project
  wants to err in.

The cost is arithmetic: one term per possible table rather than a closed form, O(min(n,
successes)) log-gamma evaluations per comparison. At this line's rates that is a few
dozen terms, and the whole M3 pattern suite — some 13 000 comparisons over 1.4 million
observations — runs in under two seconds. No dependency: `math` has everything, as every
other significance number in this repository already assumes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Verdict(Enum):
    """The three answers, and the third is not the second.

    `NOT_SIGNIFICANT` is "we looked and found nothing". `NOT_ENOUGH_DATA` is "we could
    not look". §5.5 rests on the difference: the agent says different things about them,
    and a system that collapsed the second into the first would report a clean line
    where nobody had checked.
    """

    SIGNIFICANT = "significant"
    NOT_SIGNIFICANT = "not_significant"
    NOT_ENOUGH_DATA = "not_enough_data"


@dataclass(frozen=True)
class Counts:
    """Successes out of trials. Raises ValueError unless 0 ≤ successes ≤ trials."""

    successes: int
    trials: int

    def __post_init__(self) -> None:
        if self.successes < 0 or self.trials < 0 or self.successes > self.trials:
            raise ValueError(
                "counts must satisfy 0 <= successes <= trials; got "
                f"successes={self.successes!r}, trials={self.trials!r}"
            )

    @property
    def share(self) -> float | None:
        """None when nothing was counted, rather than a 0.0 that reads like a measured
        zero — an expected share of 0 % makes every observation an infinite excess."""
        if self.trials == 0:
            return None
        return self.successes / self.trials


@dataclass(frozen=True)
class SignificanceSettings:
    """§10.3: α and the gate are configuration, and both defaults are measured.

    `alpha` 0.05 is the convention and is the number `patterns` corrects for multiplicity
    against.

    `minimum_sample` 100 is this line's. At §3.5's 1.5 % reject rate 100 parts carry 1.5
    expected rejects, so one part either way moves the observed share by two thirds of
    the base rate — below that a verdict is a report on which single part landed. It is
    also where the pooled comparison is still testing sampling noise rather than the
    pool's own spread: a clean twelve-carrier pool at 100 parts each reports a carrier in
    10 runs of 300, and by 1000 parts each in 23 of 40, because by then the noise floor's
    genuine carrier-to-carrier variation is itself detectable (`test_patterns` measures
    both ends).

    The gate is a floor on meaning, not a promise of power: at 100 parts the chance of
    catching the line's own 3σ carrier wear is 18 % over 400 runs, which is why
    `NOT_SIGNIFICANT` stays a frequent and correct answer above it.
    """

    alpha: float = 0.05
    minimum_sample: int = 100

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha < 1.0:
            raise ValueError(f"alpha must lie strictly in (0, 1); got {self.alpha!r}")
        if self.minimum_sample < 1:
            raise ValueError(
                f"minimum_sample must be at least 1; got {self.minimum_sample!r}"
            )


@dataclass(frozen=True)
class Comparison:
    """One value against its reference: everything §5.5 asks an answer to carry.

    `p_value` is None exactly when the verdict is `NOT_ENOUGH_DATA` — a number there
    would be an invitation to compare it against α somewhere downstream, which is the
    collapse `Verdict` exists to prevent.
    """

    observed: Counts
    reference: Counts
    p_value: float | None
    verdict: Verdict

    @property
    def observed_share(self) -> float | None:
        return self.observed.share

    @property
    def expected_share(self) -> float | None:
        return self.reference.share

    @property
    def sample_size(self) -> int:
        return self.observed.trials

    @property
    def effect_size(self) -> float | None:
        """Cohen's h: the difference of the arcsine-square-root transformed shares.

        Not a difference in percentage points, which is unreadable across base rates —
        one point is most of the line at a 1.5 % reject rate and nothing at all at 50 %.
        Positive means the observed share is the higher one, and the number does not move
        with the sample size, so "how large" and "how sure" stay separate answers.
        """
        observed, expected = self.observed.share, self.reference.share
        if observed is None or expected is None:
            return None
        return 2.0 * math.asin(math.sqrt(observed)) - 2.0 * math.asin(
            math.sqrt(expected)
        )


DEFAULT_SIGNIFICANCE = SignificanceSettings()
"""One shared instance of the defaults. The settings are frozen, so there is nothing a
caller could do to this that would reach another one."""


def compare_proportions(
    observed: Counts,
    reference: Counts,
    settings: SignificanceSettings = DEFAULT_SIGNIFICANCE,
) -> Comparison:
    """Compare `observed` against `reference`, two-sided.

    Two-sided because §5.5 asks for patterns and not for bad news: a lane that stopped
    producing defects is as much a pattern as one that started, and on this line it is
    likelier to be a classifier that has stopped calling than a lane that has got better.

    Both sides must clear `minimum_sample`. A reference below it is the shape a dimension
    with a single value takes — there is no rest of the pool — and the answer to that is
    that we could not look, not that nothing is there.
    """
    if (
        observed.trials < settings.minimum_sample
        or reference.trials < settings.minimum_sample
    ):
        return Comparison(observed, reference, None, Verdict.NOT_ENOUGH_DATA)

    p_value = _exact_p_value(observed, reference)
    verdict = (
        Verdict.SIGNIFICANT if p_value <= settings.alpha else Verdict.NOT_SIGNIFICANT
    )
    return Comparison(observed, reference, p_value, verdict)


# Two tables of equal probability must both be counted, and they reach here as floats
# that agree to about a part in 10^15. The tolerance is the conventional one for this
# test; without it a table and its mirror image are separated by one ulp and the p-value
# halves for reasons no data supports.
_PROBABILITY_TOLERANCE = 1e-7


def _exact_p_value(observed: Counts, reference: Counts) -> float:
    """Fisher's two-sided exact p: the total probability of every table, at these
    margins, no more probable than the one seen."""
    trials = observed.trials + reference.trials
    successes = observed.successes + reference.successes
    log_denominator = _log_binomial(trials, observed.trials)

    def log_probability(in_observed: int) -> float:
        return (
            _log_binomial(successes, in_observed)
            + _log_binomial(trials - successes, observed.trials - in_observed)
            - log_denominator
        )

    cutoff = log_probability(observed.successes) + _PROBABILITY_TOLERANCE
    lowest = max(0, observed.trials - (trials - successes))
    highest = min(observed.trials, successes)
    terms = [
        math.exp(log_term)
        for candidate in range(lowest, highest + 1)
        if (log_term := log_probability(candidate)) <= cutoff
    ]
    # The terms are a probability distribution's and sum to 1 at most, but they are
    # summed in floating point from lgamma; 1.0000000000000002 against an alpha of 1
    # would be a verdict.
    return min(1.0, math.fsum(terms))


def _log_binomial(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
