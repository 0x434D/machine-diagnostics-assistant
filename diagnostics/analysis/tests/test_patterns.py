"""§5.5 over a dimension, and the answer "nothing".

The noise floor is rebuilt here draw for draw from `simulator.noise` rather than
imported: the two stacks share no code and must not start now (§2.1), and a uniform
stand-in would make every test in this file prove something easier than the thing it
claims. The three constants below are the plant's, and a test that fails because one of
them moved is telling the truth — the sample sizes M3 needs are a consequence of the
line's reject rate and of how wide its carriers spread.

Sample sizes are stated in parts per carrier and are what they are for a reason given at
each test. 100 parts per carrier is two hours of production on twelve carriers at §3.2's
6 s takt; 1000 is the whole 33 h history.
"""

from __future__ import annotations

import math
import random
import statistics

import pytest
from analysis.patterns import (
    Correction,
    Dimension,
    Observation,
    PatternSettings,
    find_patterns,
)
from analysis.significance import SignificanceSettings, Verdict

LOG_SIGMA = 0.35
"""`simulator.config.carrier_quality_log_sigma`."""
REJECT_RATE = 0.015
"""`simulator.config.reject_rate`."""
DEFECT_DRAWS = 12
"""`len(LANES) * len(DEFECT_CLASSES)` — the independent draws `simulator.noise` makes
per part, and not the size of the defect vocabulary."""

CARRIERS = 12


def _carrier_qualities(rng: random.Random, carriers: int) -> list[float]:
    """`simulator.noise.NoiseFloor.carrier_quality`, reproduced draw for draw.

    Lognormal, then scaled so the mean across the pool is exactly 1 — the scaling is
    load-bearing here as it is there: without it each seed's pool scraps at its own rate
    and the expected share this module compares against would be a property of the seed.
    """
    raw = [math.exp(rng.gauss(0.0, LOG_SIGMA)) for _ in range(carriers)]
    scale = 1.0 / statistics.fmean(raw)
    return [quality * scale for quality in raw]


def _part_rate(quality: float) -> float:
    """The chance that a part on a carrier of this quality carries any defect.

    `simulator.noise.class_propensity` scales a per-draw rate, so a 2.86× carrier is a
    2.82× part rate and not a 2.86× one. The difference is small and reproducing it is
    free; inventing a linear model instead would put a quiet 1.5 % error between this
    file's generator and the plant it claims to imitate.
    """
    per_draw = -math.expm1(math.log1p(-REJECT_RATE) / DEFECT_DRAWS)
    return -math.expm1(DEFECT_DRAWS * math.log1p(-per_draw * quality))


def _run(
    rng: random.Random, qualities: list[float], parts_per_carrier: int
) -> list[Observation]:
    observations: list[Observation] = []
    for index, quality in enumerate(qualities):
        rate = _part_rate(quality)
        carrier = f"C{index:02d}"
        observations.extend(
            Observation(carrier=carrier, outcome=rng.random() < rate)
            for _ in range(parts_per_carrier)
        )
    return observations


def _clean_run(seed: int, parts_per_carrier: int) -> list[Observation]:
    rng = random.Random(seed)
    return _run(rng, _carrier_qualities(rng, CARRIERS), parts_per_carrier)


def _run_with_a_worn_carrier(
    seed: int, parts_per_carrier: int, worn: dict[int, float]
) -> list[Observation]:
    rng = random.Random(seed)
    qualities = _carrier_qualities(rng, CARRIERS)
    for carrier, multiplier in worn.items():
        qualities[carrier] *= multiplier
    return _run(rng, qualities, parts_per_carrier)


def _runs_with_a_finding(
    seeds: range, parts_per_carrier: int, *settings: PatternSettings
) -> list[int]:
    """How many of `seeds` clean runs report any pattern, under each of `settings`.

    Every setting is applied to the same run before the next seed is drawn, because the
    comparison between two corrections is only worth anything on identical data.
    """
    found = [0] * len(settings)
    for seed in seeds:
        observations = _clean_run(seed, parts_per_carrier)
        for index, setting in enumerate(settings):
            if find_patterns(observations, Dimension.CARRIER, setting).significant:
                found[index] += 1
    return found


def _observation(dimension: Dimension, value: str, *, outcome: bool) -> Observation:
    """One observation that takes `value` along `dimension` and a constant along the
    other three, so a grouping that read the wrong field would find a single value and
    have nothing to compare it against."""
    values = dict.fromkeys(Dimension, "the-same-everywhere")
    values[dimension] = value
    return Observation(
        carrier=values[Dimension.CARRIER],
        lane=values[Dimension.LANE],
        defect_class=values[Dimension.DEFECT_CLASS],
        time_bucket=values[Dimension.TIME_BUCKET],
        outcome=outcome,
    )


def test_a_worn_carrier_is_found_and_is_the_only_thing_found() -> None:
    """Carrier 7 at four times the pool's propensity, 300 parts each, seed 7000.

    The eleven others are drawn from the noise floor and are not identical to each
    other, which is the whole difficulty: 16 rejects against a pool of 47 in 3300 is a
    finding, and the second-worst carrier's 8 in 300 — more than five times the best
    carrier's 1 — is not.
    """
    observations = _run_with_a_worn_carrier(7000, 300, {7: 4.0})
    report = find_patterns(observations, Dimension.CARRIER)

    assert [pattern.value for pattern in report.significant] == ["C07"]
    worn = report.significant[0]
    assert worn.comparison.observed_share == pytest.approx(16 / 300)
    assert worn.comparison.expected_share == pytest.approx(47 / 3300)
    assert worn.comparison.sample_size == 300
    assert worn.adjusted_p_value is not None
    assert worn.adjusted_p_value < 0.01
    assert worn.comparison.effect_size is not None
    assert worn.comparison.effect_size > 0
    assert len(report.patterns) == CARRIERS


def test_the_noise_floor_alone_is_not_a_pattern() -> None:
    """The test that separates M3 from a `GROUP BY`.

    Twelve carriers drawn from the shipped noise floor, nothing injected, 100 parts
    each, 300 seeds. The correct answer is "nothing" every time, and this module gives
    it in 290 of the 300 runs — the ten it does not are what an α of 0.05 promises.

    The uncorrected rate over the same 300 runs is 115: without the correction a clean
    line would be handed a story to tell in better than one shift in three.
    """
    seeds = range(1000, 1300)
    corrected, uncorrected = _runs_with_a_finding(
        seeds, 100, PatternSettings(), PatternSettings(correction=Correction.NONE)
    )

    assert corrected <= 0.10 * len(seeds), (
        f"{corrected} of {len(seeds)} clean runs reported a carrier; measured 10 when "
        "this was written, and α=0.05 is what the bar is"
    )
    assert uncorrected >= 3 * corrected, (
        f"correcting for twelve comparisons changed {uncorrected} runs into "
        f"{corrected}; if it stops mattering, the correction has been defeated"
    )


def test_the_pools_own_spread_becomes_a_finding_in_a_long_enough_window() -> None:
    """The limit of what a share-against-the-pool test can be asked, stated as a test.

    Over the full 33 h history — 1000 parts per carrier — the same clean pool produces a
    significant carrier in 23 of 40 runs. That is not a defect in the test and not a
    false positive: at that sample size a carrier sitting two sigma up the noise floor
    really does differ from the rest of the pool, and the arithmetic says so correctly.

    It is the reason §5.5 asks for the effect size and the expected share next to the
    verdict rather than a verdict alone. Significance answers *is this real*; only the
    magnitude answers *is this wear*, and separating the two is the knowledge base's
    job (§6), not this module's. A milestone that wants "carrier 7 is worn" out of a
    33 h window needs a test whose null is the carrier population rather than the
    pooled parts — a dispersion-aware model this project does not have and cannot
    estimate at 1.5 % and a handful of rejects per carrier.
    """
    seeds = range(3000, 3040)
    (found,) = _runs_with_a_finding(seeds, 1000, PatternSettings())
    assert found >= 0.35 * len(seeds), (
        f"only {found} of {len(seeds)} full-history runs found the pool's own spread; "
        "measured 23 when this was written, and if it has fallen to nothing then the "
        "test has lost the power that makes it worth running at all"
    )


def test_twelve_null_carriers_produce_a_finding_at_alpha_not_twelve_alpha() -> None:
    """Twelve identical carriers, so every finding is a false one, 300 parts each.

    Uncorrected, 54 of 150 runs report a carrier — the 1 − 0.95¹² ≈ 46 % that testing
    twelve hypotheses at α=0.05 buys, less what Fisher's discreteness holds back.
    Corrected, 4 of 150.
    """
    settings = PatternSettings()
    uncorrected = PatternSettings(correction=Correction.NONE)
    seeds = range(5000, 5150)

    corrected_runs = 0
    uncorrected_runs = 0
    for seed in seeds:
        rng = random.Random(seed)
        observations = _run(rng, [1.0] * CARRIERS, 300)
        if find_patterns(observations, Dimension.CARRIER, settings).significant:
            corrected_runs += 1
        if find_patterns(observations, Dimension.CARRIER, uncorrected).significant:
            uncorrected_runs += 1

    assert corrected_runs <= 0.08 * len(seeds), (
        f"{corrected_runs} of {len(seeds)} null runs reported a carrier against an "
        "α of 0.05; measured 4 when this was written"
    )
    assert uncorrected_runs >= 0.25 * len(seeds), (
        f"only {uncorrected_runs} of {len(seeds)} null runs reported a carrier without "
        "the correction; measured 54, and if this has collapsed the two arms no longer "
        "differ and the test proves nothing"
    )


def test_benjamini_hochberg_finds_a_third_effect_bonferroni_misses() -> None:
    """What the correction choice buys, on a run with three worn carriers.

    Carriers 2, 5 and 7 at three times the pool, 300 parts each, seed 20017: raw
    p-values 1.2e-2, 2.6e-3 and 1.3e-5. Bonferroni's flat 0.05/12 = 4.2e-3 reaches two
    of them; Benjamini-Hochberg's step-up reaches all three. Neither reports anything
    that is not there.
    """
    observations = _run_with_a_worn_carrier(20017, 300, {2: 3.0, 5: 3.0, 7: 3.0})

    step_up = find_patterns(observations, Dimension.CARRIER)
    flat = find_patterns(
        observations,
        Dimension.CARRIER,
        PatternSettings(correction=Correction.BONFERRONI),
    )

    assert [pattern.value for pattern in step_up.significant] == ["C07", "C05", "C02"]
    assert [pattern.value for pattern in flat.significant] == ["C07", "C05"]


def test_below_the_gate_every_value_says_not_enough_data() -> None:
    """Half an hour of production over twelve carriers: 50 parts each, 0.75 expected
    rejects. Nothing here is "not significant" — nobody looked."""
    observations = _clean_run(1000, 50)
    report = find_patterns(observations, Dimension.CARRIER)

    assert report.significant == ()
    assert [pattern.verdict for pattern in report.patterns] == [
        Verdict.NOT_ENOUGH_DATA
    ] * CARRIERS
    assert Verdict.NOT_SIGNIFICANT not in [p.verdict for p in report.patterns]
    assert all(pattern.adjusted_p_value is None for pattern in report.patterns)


def test_a_dimension_with_one_value_has_nothing_to_compare_it_against() -> None:
    """One carrier, plenty of parts: the rest of the pool is empty, and an expected
    share of zero would make any defect at all look like an infinite excess."""
    observations = [
        Observation(carrier="C00", outcome=index % 50 == 0) for index in range(400)
    ]
    report = find_patterns(observations, Dimension.CARRIER)

    assert len(report.patterns) == 1
    only = report.patterns[0]
    assert only.verdict is Verdict.NOT_ENOUGH_DATA
    assert only.comparison.expected_share is None
    assert only.comparison.observed_share == pytest.approx(8 / 400)


def test_an_empty_window_is_a_report_with_nothing_in_it() -> None:
    report = find_patterns([], Dimension.CARRIER)
    assert report.patterns == ()
    assert report.significant == ()
    assert report.unattributed == 0


@pytest.mark.parametrize("dimension", list(Dimension))
def test_every_dimension_groups_by_its_own_value(dimension: Dimension) -> None:
    """§5.5 names four dimensions and they share one code path, so the risk is a
    dimension that silently groups by another field. Each observation carries the same
    constant along the other three: read the wrong one and there is a single value and
    no comparison at all.

    Both values come back significant, and that is arithmetic rather than an accident —
    with two values each is the other's reference and the 2×2 table is the same table
    either way round. The effect sizes point in opposite directions, which is what says
    which of the two is the elevated one.
    """
    observations = [
        _observation(dimension, "A", outcome=index % 100 == 0) for index in range(600)
    ] + [_observation(dimension, "B", outcome=index % 5 == 0) for index in range(600)]

    report = find_patterns(observations, dimension)

    assert report.dimension is dimension
    assert sorted(pattern.value for pattern in report.significant) == ["A", "B"]
    effects = {
        pattern.value: pattern.comparison.effect_size for pattern in report.patterns
    }
    assert effects["A"] is not None
    assert effects["B"] is not None
    assert effects["A"] < 0 < effects["B"]


def test_observations_with_no_value_are_counted_not_silently_dropped() -> None:
    """A part whose carrier the gateway never saw — the horizon stub in the read layer
    is exactly this — belongs to no carrier and cannot be compared. Reporting how many
    there were is what keeps a half-attributed window from reading as a whole one."""
    attributed = [
        Observation(carrier="C00", outcome=index % 20 == 0) for index in range(400)
    ]
    orphans = [Observation(carrier=None, outcome=True) for _ in range(7)]

    report = find_patterns([*attributed, *orphans], Dimension.CARRIER)
    assert report.unattributed == 7
    assert [pattern.value for pattern in report.patterns] == ["C00"]
    assert report.patterns[0].comparison.sample_size == 400


def test_the_report_does_not_depend_on_the_order_of_the_observations() -> None:
    """The later task feeds this from SQL, and SQL promises no order without ORDER BY.
    A report that moved with the row order would be a defect nobody could reproduce."""
    observations = _run_with_a_worn_carrier(7000, 300, {7: 4.0})
    shuffled = list(observations)
    random.Random(1).shuffle(shuffled)

    assert find_patterns(observations, Dimension.CARRIER) == find_patterns(
        shuffled, Dimension.CARRIER
    )


def test_the_gate_and_alpha_are_configuration() -> None:
    """§10.3, at the level the endpoint will pass them in."""
    observations = _run_with_a_worn_carrier(7000, 300, {7: 4.0})

    gated = find_patterns(
        observations,
        Dimension.CARRIER,
        PatternSettings(significance=SignificanceSettings(minimum_sample=400)),
    )
    assert gated.significant == ()
    assert all(p.verdict is Verdict.NOT_ENOUGH_DATA for p in gated.patterns)

    strict = find_patterns(
        observations,
        Dimension.CARRIER,
        PatternSettings(significance=SignificanceSettings(alpha=1e-9)),
    )
    assert strict.significant == ()
    assert all(p.verdict is Verdict.NOT_SIGNIFICANT for p in strict.patterns)
