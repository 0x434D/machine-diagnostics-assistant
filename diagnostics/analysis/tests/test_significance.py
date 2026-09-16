"""The arithmetic, pinned against numbers that exist outside this repository.

Fisher's own tea-tasting table is here because every other assertion in this file is a
number the module produced: a test suite that only checks a function against itself
proves the function is deterministic and nothing else. 34/70 is Fisher's, from 1935, and
is hand-checkable from the hypergeometric terms in one line of arithmetic.
"""

from __future__ import annotations

import math

import pytest
from analysis.significance import (
    Counts,
    SignificanceSettings,
    Verdict,
    compare_proportions,
)

UNGATED = SignificanceSettings(minimum_sample=1)
"""For the tables that exist to pin arithmetic rather than to pass a gate."""


def test_the_tea_tasting_table_gives_fishers_own_number() -> None:
    """[[3, 1], [1, 3]]: two-sided p = 34/70.

    The four tables at most as probable as the observed one are k ∈ {0, 1, 3, 4} at
    1/70, 16/70, 16/70 and 1/70; k = 2 is more probable (36/70) and is excluded.
    """
    result = compare_proportions(Counts(3, 4), Counts(1, 4), UNGATED)
    assert result.p_value == pytest.approx(34 / 70)


def test_a_materially_elevated_share_is_significant() -> None:
    result = compare_proportions(Counts(16, 300), Counts(47, 3300), UNGATED)
    assert result.verdict is Verdict.SIGNIFICANT
    assert result.p_value is not None
    assert result.p_value < 0.001
    assert result.observed_share == pytest.approx(16 / 300)
    assert result.expected_share == pytest.approx(47 / 3300)
    assert result.sample_size == 300


def test_a_share_that_matches_the_reference_is_not_significant() -> None:
    result = compare_proportions(Counts(6, 400), Counts(60, 4000), UNGATED)
    assert result.verdict is Verdict.NOT_SIGNIFICANT
    assert result.p_value is not None
    assert result.p_value > 0.5
    assert result.effect_size == pytest.approx(0.0, abs=1e-12)


def test_a_sample_below_the_gate_is_not_enough_data_and_not_not_significant() -> None:
    """The distinction §5.5 turns on, asserted as a distinction and not as a verdict.

    The same share, the same reference: at n=38 the answer is that we could not look,
    at n=400 that we looked and found something. Collapsing the first into "not
    significant" would tell the agent the line is clean when nobody has checked.
    """
    settings = SignificanceSettings(minimum_sample=100)
    reference = Counts(60, 4000)

    could_not_look = compare_proportions(Counts(2, 38), reference, settings)
    looked_and_found_nothing = compare_proportions(Counts(6, 400), reference, settings)
    looked_and_found_something = compare_proportions(
        Counts(21, 400), reference, settings
    )

    assert could_not_look.verdict is Verdict.NOT_ENOUGH_DATA
    assert looked_and_found_nothing.verdict is Verdict.NOT_SIGNIFICANT
    assert looked_and_found_something.verdict is Verdict.SIGNIFICANT
    assert (
        len(
            {
                could_not_look.verdict,
                looked_and_found_nothing.verdict,
                looked_and_found_something.verdict,
            }
        )
        == 3
    )

    # No p-value at all below the gate, rather than one nobody may act on: a number in
    # this field is an invitation to compare it against alpha somewhere downstream.
    assert could_not_look.p_value is None
    # The share and the sample size survive the gate — §5.5 wants "n=38" in the answer.
    assert could_not_look.observed_share == pytest.approx(2 / 38)
    assert could_not_look.sample_size == 38


def test_a_reference_below_the_gate_is_also_not_enough_data() -> None:
    """A dimension with one value, in its arithmetic form: nothing to compare against.

    Counts(0, 0) is what "the rest of the pool" comes to when the pool has one member.
    The answer is that we could not look, not a ZeroDivisionError and not a share of
    zero — an expected share of 0 % would make every value look infinitely elevated.
    """
    result = compare_proportions(Counts(7, 400), Counts(0, 0), UNGATED)
    assert result.verdict is Verdict.NOT_ENOUGH_DATA
    assert result.expected_share is None
    assert result.effect_size is None
    assert result.p_value is None
    assert result.observed_share == pytest.approx(7 / 400)


def test_the_exact_test_refuses_what_the_normal_approximation_would_have_claimed() -> (
    None
):
    """The measurement that chose the test, reduced to one table.

    5 rejects in 120 parts against 15 in 1000 is the shape this line produces all day:
    a 1.5 % base rate puts single-digit counts in the observed cell, which is where the
    normal approximation's error is largest and always in the direction of claiming too
    much. The pooled two-proportion z makes this significant at α=0.05. It is not.
    """
    observed, reference = Counts(5, 120), Counts(15, 1000)
    result = compare_proportions(observed, reference, UNGATED)

    pooled = (observed.successes + reference.successes) / (
        observed.trials + reference.trials
    )
    standard_error = math.sqrt(
        pooled * (1 - pooled) * (1 / observed.trials + 1 / reference.trials)
    )
    z = (5 / 120 - 15 / 1000) / standard_error
    z_p_value = math.erfc(abs(z) / math.sqrt(2))

    assert z_p_value < 0.05, "the rejected alternative would have called this a finding"
    assert result.p_value is not None
    assert result.p_value > 0.05
    assert result.verdict is Verdict.NOT_SIGNIFICANT


def test_an_unusually_clean_value_is_a_pattern_too() -> None:
    """Two-sided: a lane that stopped producing defects is as much a pattern as one
    that started, and on this line it is more likely to be a stuck classifier than good
    news."""
    result = compare_proportions(Counts(0, 300), Counts(150, 1500), UNGATED)
    assert result.verdict is Verdict.SIGNIFICANT
    assert result.effect_size is not None
    assert result.effect_size < 0


def test_the_effect_size_does_not_move_with_the_sample_size() -> None:
    """Cohen's h, so that "how large" and "how sure" stay separate numbers.

    The p-value falls as n grows; the effect size must not, or the agent has no way to
    tell a big difference from a well-measured one.
    """
    small = compare_proportions(Counts(20, 400), Counts(100, 4000), UNGATED)
    large = compare_proportions(Counts(200, 4000), Counts(1000, 40000), UNGATED)
    assert small.effect_size is not None
    assert large.effect_size is not None
    assert small.effect_size == pytest.approx(large.effect_size)
    assert small.p_value is not None
    assert large.p_value is not None
    assert large.p_value < small.p_value


def test_alpha_and_the_gate_are_configuration() -> None:
    """§10.3. The same counts, three settings, three verdicts."""
    observed, reference = Counts(5, 120), Counts(15, 1000)
    p_value = compare_proportions(observed, reference, UNGATED).p_value
    assert p_value is not None
    assert 0.05 < p_value < 0.1  # so the two alphas below sit either side of it

    strict = compare_proportions(
        observed, reference, SignificanceSettings(alpha=0.05, minimum_sample=1)
    )
    lenient = compare_proportions(
        observed, reference, SignificanceSettings(alpha=0.1, minimum_sample=1)
    )
    gated = compare_proportions(
        observed, reference, SignificanceSettings(alpha=0.1, minimum_sample=200)
    )
    assert strict.verdict is Verdict.NOT_SIGNIFICANT
    assert lenient.verdict is Verdict.SIGNIFICANT
    assert gated.verdict is Verdict.NOT_ENOUGH_DATA


def test_the_same_counts_give_the_same_comparison() -> None:
    first = compare_proportions(Counts(16, 300), Counts(47, 3300), UNGATED)
    second = compare_proportions(Counts(16, 300), Counts(47, 3300), UNGATED)
    assert first == second


def test_a_p_value_is_a_probability_even_at_the_extremes() -> None:
    """Every table's terms sum to 1 by construction, but they are summed in floating
    point from lgamma, and a p-value of 1.0000000000000002 is a verdict flip waiting
    for an alpha of 1."""
    for observed, reference in (
        (Counts(0, 500), Counts(0, 5000)),
        (Counts(500, 500), Counts(5000, 5000)),
        (Counts(1, 2), Counts(1, 2)),
        (Counts(250, 500), Counts(2500, 5000)),
    ):
        result = compare_proportions(observed, reference, UNGATED)
        assert result.p_value is not None
        assert 0.0 <= result.p_value <= 1.0


@pytest.mark.parametrize(
    ("successes", "trials"),
    [(5, 4), (-1, 10), (0, -1)],
)
def test_impossible_counts_are_rejected(successes: int, trials: int) -> None:
    with pytest.raises(ValueError, match="successes"):
        Counts(successes, trials)


@pytest.mark.parametrize(
    ("alpha", "minimum_sample"),
    [(0.0, 100), (1.0, 100), (-0.1, 100), (0.05, 0)],
)
def test_impossible_settings_are_rejected(alpha: float, minimum_sample: int) -> None:
    with pytest.raises(ValueError):
        SignificanceSettings(alpha=alpha, minimum_sample=minimum_sample)
