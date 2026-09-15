"""§3.5's permanent noise floor, and the one measurement that decides scenario 4.

The numbers in `test_one_worn_carrier_is_distinguishable_from_the_baseline_spread` are
the point of this file. A ratio between background variation and injected wear that is
never measured has moved M2c's hard part into M3 without saying so -- too tight and
finding carrier 7 is a `GROUP BY`, too loose and it is unwinnable.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import build_running_line
from simulator.config import Settings
from simulator.faults import Fault, FaultKind, FaultSet
from simulator.inspection_client import InspectionClient
from simulator.noise import NoiseFloor
from simulator.packml import State
from simulator.scenarios import scenario

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
NOMINAL_WORK = Settings().press_nominal_work
"""A part pressed at every nominal, so the `gap` propensity is unscaled.

Nothing in this file is about the press: the baseline scrap and the carrier spread
measured here are what a nominally joined part carries, and `test_scenarios` is where
a press that did less work changes the answer.
"""

WORN_CARRIER = Settings().worn_carrier_id
"""§3.5 scenario 4 names carrier 7, and `config` is where the scenario reads it from."""

WEAR_CLASSES = ("misalignment", "scratch")
"""The two classes §3.5 says concentrate on a worn carrier -- and the query M3 will run,
which is what the separation below has to be measured on rather than on scrap overall."""

# 33 h at the ~6 s takt, which is what the shipped history depth produces and the depth
# the separation below is quoted at. Fewer parts per carrier is a wider binomial spread
# on every carrier's count, so an effect size measured at a shorter depth is a different
# number and not a smaller version of this one.
PRODUCTION_PARTS = 19_800


def _wear(settings: Settings) -> FaultSet:
    """§3.5's scenario 4, as it actually ships.

    The shipped scenario rather than a fault built here to look like it, and the
    difference is the whole value of this file: the separation measured below is the
    number that decides whether M3 inherits a findable scenario or an unwinnable one, and
    a hand-built copy would keep reporting it after the scenario's own magnitude moved.
    Its offset is dropped -- `T0` stands in as the origin -- because nothing in this file
    runs a line, so every part here is drawn at one instant well inside the window.
    """
    injected = scenario(4, settings).faults
    assert [fault.kind for fault in injected] == [FaultKind.CARRIER_WEAR]
    return FaultSet(
        [Fault(fault.kind, timedelta(0), fault.params) for fault in injected], T0
    )


def _rates_by_carrier(
    client: InspectionClient, settings: Settings, classes: tuple[str, ...] | None
) -> list[float]:
    """Each carrier's share of parts carrying one of `classes` (all classes if None).

    Carriers round-robin because the pool is FIFO and no station holds one between
    cycles, so `part % carrier_count` is the assignment the line actually makes.
    """
    pool = settings.carrier_count
    hits = [0] * pool
    seen = [0] * pool
    at = T0 + timedelta(hours=4)
    for index in range(PRODUCTION_PARTS):
        carrier = index % pool
        defects = client.truth_for(f"A-{index:08d}", carrier, NOMINAL_WORK, at)
        seen[carrier] += 1
        if defects if classes is None else set(defects) & set(classes):
            hits[carrier] += 1
    return [hits[i] / seen[i] for i in range(pool)]


def _effect(rates: list[float], carrier: int) -> float:
    """Cohen's d for `carrier` against the other seventeen -- **leave-one-out**: the
    mean and the spread it is measured against exclude the carrier being tested.

    Leave-one-out because a carrier that is genuinely out of family drags the pool's own
    mean and spread towards itself, which is the estimate an analysis would be making if
    it compared a suspect carrier to a statistic the suspect is inside. Every figure this
    file quotes as a *d* is this one, so they are comparable to each other.
    """
    others = [rate for index, rate in enumerate(rates) if index != carrier]
    return (rates[carrier] - statistics.fmean(others)) / statistics.stdev(others)


def _worst_other(rates: list[float], carrier: int) -> tuple[int, float]:
    """The carrier other than `carrier` with the largest `_effect`, and that effect.

    What an agent that simply took the highest carrier would be looking at, scored the
    same way as the suspect so the two numbers can be put side by side.
    """
    candidates = {
        index: _effect(rates, index) for index in range(len(rates)) if index != carrier
    }
    top = max(candidates, key=lambda index: candidates[index])
    return top, candidates[top]


# --- genuine carrier-to-carrier variation ---------------------------------------------


@pytest.mark.asyncio
async def test_carrier_quality_varies_without_any_fault_injected() -> None:
    """Without this, finding carrier 7 is a GROUP BY and §3.5's significance
    requirement is decoration."""
    settings = Settings()
    async with httpx.AsyncClient() as http:
        client = InspectionClient(settings, http)
        rates = _rates_by_carrier(client, settings, None)

    spread = statistics.stdev(rates) / statistics.fmean(rates)
    assert spread > 0.2, (
        f"carriers scrap at {spread:.1%} relative spread with nothing injected; below "
        "this the background is sampling noise rather than a property of the carrier"
    )


@pytest.mark.asyncio
async def test_a_clean_run_still_has_a_carrier_that_looks_worst() -> None:
    """§3.5's actual requirement: the agent must be able to say *that is within normal
    spread*. It only has to say it because a clean run always has a highest carrier, and
    this is what puts one there.

    Two statistics, because they are different numbers and the difference matters to
    whoever reads them. Against the whole eighteen-carrier pool the worst carrier is
    carrier 10 at +2.09 sigma; scored leave-one-out -- the same `_effect` that gives
    carrier 7 its +5.84 under scenario 4 -- it is +2.52. The second is the comparable
    one and is what the ratio was set against.
    """
    settings = Settings()
    async with httpx.AsyncClient() as http:
        client = InspectionClient(settings, http)
        rates = _rates_by_carrier(client, settings, WEAR_CLASSES)

    pooled = (max(rates) - statistics.fmean(rates)) / statistics.stdev(rates)
    _, leave_one_out = _worst_other(rates, WORN_CARRIER)
    assert pooled > 1.5, (
        f"the highest carrier on a clean run sits at only {pooled:.2f} sigma against "
        "the pool; nothing here would tempt a wrong answer, and the significance test "
        "M3 needs would be decoration"
    )
    # Leave-one-out is the larger of the two by construction -- the suspect is out of
    # the mean and out of the spread -- so a run where it is not has a pool whose
    # spread is being set by something other than the carriers.
    assert leave_one_out > pooled


@pytest.mark.asyncio
async def test_the_carrier_qualities_redistribute_scrap_rather_than_moving_it() -> None:
    """The noise floor must not quietly move the number it is a noise floor around.

    Drawn from a lognormal and then scaled across the pool: without the scaling the
    shipped seed's eighteen draws averaged 0.865 and the line scrapped at 1.37 % while
    `reject_rate` said 1.5 %.
    """
    settings = Settings()
    noise = NoiseFloor(settings, settings.seed)
    qualities = [noise.carrier_quality(i) for i in range(settings.carrier_count)]
    assert statistics.fmean(qualities) == pytest.approx(1.0, abs=1e-9)

    async with httpx.AsyncClient() as http:
        client = InspectionClient(settings, http)
        rates = _rates_by_carrier(client, settings, None)
    # Sampling on 19,800 parts is +-0.00017 at one sigma, so 15 % of the rate is a wide
    # band deliberately: what this refuses is a systematic shift, not a fluctuation.
    assert statistics.fmean(rates) == pytest.approx(settings.reject_rate, rel=0.15)


@pytest.mark.asyncio
async def test_one_worn_carrier_is_distinguishable_from_the_baseline_spread() -> None:
    """The measurement that decides whether scenario 4 is findable.

    **Measured at the shipped settings** -- seed 20260912, 18 carriers,
    `carrier_quality_log_sigma` 0.35, `carrier_wear_sigmas` 3.0 (a factor of 2.86), over
    19,800 parts, so 1,100 per carrier:

    Every *d* below is `_effect`'s leave-one-out statistic, so they compare directly.

    * the pool's own quality spread is 28.7 % relative (the distribution's is 36.2 %;
      eighteen draws is a small sample and this is the spread the line actually has);
    * on a clean run the `misalignment | scratch` rate is 0.571 % across the pool with a
      spread of 0.292 %; the worst carrier is carrier 10, at **d = +2.52** leave-one-out
      (+2.09 sigma against the whole pool) -- which is why "the top carrier is the
      culprit" has to be wrong sometimes;
    * with the wear injected, carrier 7 reaches 2.18 % -- **d = +5.84**, while the
      worst *other* carrier in the same run falls back to +1.24;
    * both of §3.5's classes rise on it, separately: misalignment d = +3.75, scratch
      d = +4.98. That pair is a pair **per carrier**, not per part -- see
      `inspection_client.TRUTH_DRAWS_PER_PART` and the test below;
    * measured on scrap overall rather than on the two classes, d = +3.99;
    * the line's own scrap rate moves 1.601 % -> 1.667 %. Scenario 4 is therefore
      invisible at line level and visible per carrier, which is what §3.5's "no stop"
      expectation means.

    So the ratio is loose enough that a clean run still has a worst-looking carrier at
    +2.52, and tight enough that carrier 7 clears it by more than double. A significance
    test separates them; an `ORDER BY ... LIMIT 1` does not.
    """
    settings = Settings()
    async with httpx.AsyncClient() as http:
        clean = _rates_by_carrier(
            InspectionClient(settings, http), settings, WEAR_CLASSES
        )
        worn = _rates_by_carrier(
            InspectionClient(settings, http, faults=_wear(settings)),
            settings,
            WEAR_CLASSES,
        )

    effect = _effect(worn, WORN_CARRIER)
    _, worst_clean = _worst_other(clean, WORN_CARRIER)
    _, worst_worn = _worst_other(worn, WORN_CARRIER)

    assert effect > 3.0, (
        f"carrier 7 separates from the pack by only d={effect:.2f}: at "
        f"carrier_wear_sigmas={settings.carrier_wear_sigmas} M3 inherits a scenario "
        "whose answer is inside the noise"
    )
    assert effect > 2 * worst_clean, (
        f"carrier 7 reaches d={effect:.2f} while a clean run's worst carrier already "
        f"reaches {worst_clean:.2f}: too close for a significance test to mean anything"
    )
    assert effect > 2 * worst_worn, (
        f"the worst other carrier in the injected run reaches {worst_worn:.2f} against "
        f"carrier 7's {effect:.2f}: the docstring's claim that nothing else comes near "
        "it is what this checks"
    )
    assert worn[WORN_CARRIER] > max(
        rate for index, rate in enumerate(worn) if index != WORN_CARRIER
    )
    # And the wear did not simply raise every carrier, which is the line-wide drift
    # scenario 4 exists to be told apart from.
    assert [round(rate, 6) for i, rate in enumerate(worn) if i != WORN_CARRIER] == [
        round(rate, 6) for i, rate in enumerate(clean) if i != WORN_CARRIER
    ]


@pytest.mark.asyncio
async def test_a_worn_carriers_two_classes_rise_on_the_carrier_not_on_one_part() -> (
    None
):
    """DP-02 keys on a class *pair*, and this is the shape the plant actually emits it
    in: both of §3.5's wear classes elevated on carrier 7, each on its own.

    **Not two classes on one part.** D11's wording reads that way and the draws can
    express it, but at the shipped ratio the plant does not produce it: P(misalignment)
    and P(scratch) are each 0.0072 per part on the worn carrier and independent, so
    P(both) is 5.2e-5 -- 0.06 parts across carrier 7's 1,100 in a 33 h run, and a full
    run yields 0. Getting it to ten parts needs a wear factor near 39 instead of 2.86,
    which puts carrier 7 at a 19 % scrap rate against the line's 1.5 %: a `GROUP BY`
    with no test needed, and the ratio `carrier_wear_sigmas` exists to set would be
    gone. Recorded here because Task 7 would otherwise assert a per-part pair that never
    appears. `inspection_client.TRUTH_DRAWS_PER_PART` carries the same note.
    """
    settings = Settings()
    async with httpx.AsyncClient() as http:
        client = InspectionClient(settings, http, faults=_wear(settings))
        effects = {
            name: _effect(_rates_by_carrier(client, settings, (name,)), WORN_CARRIER)
            for name in WEAR_CLASSES
        }

    for name, effect in effects.items():
        assert effect > 3.0, (
            f"{name} rises on carrier 7 by only d={effect:.2f}. DP-02 is keyed on the "
            f"pair, so a scenario where only one of {list(WEAR_CLASSES)} separates is "
            "not the pattern it is meant to match"
        )


# --- baseline scrap ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_baseline_scrap_rate_is_unrelated_to_any_injected_fault() -> None:
    """§3.5's words. If baseline scrap drew from the same stream as a fault, a
    scenario's signal would be partly its own noise.

    The observable form: a fault moves a *threshold* and never a draw, so every part
    that scrapped on the clean run still scraps under the fault. A fault that re-rolled
    the draw would turn some of them good again -- and the scenario's evidence would
    then be partly the absence of defects it had itself removed.
    """
    settings = Settings()
    at = T0 + timedelta(hours=4)
    async with httpx.AsyncClient() as http:
        clean = InspectionClient(settings, http)
        worn = InspectionClient(settings, http, faults=_wear(settings))

        moved = 0
        for index in range(4_000):
            carrier = index % settings.carrier_count
            part = f"A-{index:08d}"
            before = set(clean.truth_for(part, carrier, NOMINAL_WORK, at))
            after = set(worn.truth_for(part, carrier, NOMINAL_WORK, at))
            assert before <= after, (
                f"{part} on carrier {carrier} lost {sorted(before - after)} when a "
                "fault was injected: the fault reached the draw and not the threshold"
            )
            moved += len(after - before)

    assert moved > 0, "the fault changed nothing, so this asserted nothing"


@pytest.mark.asyncio
async def test_the_same_draws_come_back_for_a_part_however_many_came_before() -> None:
    """§3.6, and what lets one client serve catch-up and live alike. Asserted on the
    draws rather than on the client, because the property is the stream's."""
    settings = Settings()
    noise = NoiseFloor(settings, settings.seed)
    once = noise.scrap_draws("A-00000099", 12)
    for index in range(50):
        noise.scrap_draws(f"A-{index:08d}", 12)
    assert noise.scrap_draws("A-00000099", 12) == once


# --- micro-stops ----------------------------------------------------------------------


def test_micro_stops_are_brief_and_clear_themselves() -> None:
    """§3.5: brief jams every ~20 min. A micro-stop that needed an operator would be
    a Held, not noise, and would pollute every cause-candidate query."""
    settings = Settings()
    noise = NoiseFloor(settings, settings.seed)
    code = "S3_Inspection"
    cycles = 20_000
    stops = [
        (cycle, noise.micro_stop_seconds(code, cycle))
        for cycle in range(cycles)
        if noise.micro_stop_seconds(code, cycle) > 0.0
    ]

    assert stops, "no micro-stop in 20,000 cycles"
    for _, seconds in stops:
        assert settings.micro_stop_min_seconds <= seconds
        assert seconds <= settings.micro_stop_max_seconds

    # Brief means brief against the thing that makes a stoppage propagate: a full buffer
    # drains in buffer_capacity x takt, so a jam shorter than that cannot on its own
    # produce the chain §5.4 categorises a real stoppage by.
    drain = settings.buffer_capacity * settings.takt_seconds
    assert max(seconds for _, seconds in stops) < drain

    # "Clears itself" is asserted where it is observable -- on a running line, in
    # test_a_micro_stop_leaves_the_line_running_and_holds_nothing. It is NOT that the
    # cycle after a jam is always quiet: the draws are independent and adjacent jams do
    # occur (S1 at cycles 154,248 and 154,249 within 200,000, measured). An assertion
    # that they do not would hold on this seed and mean nothing.

    # And they arrive at roughly §3.5's rate. Four stations share the configured
    # line-wide interval, so one station sees a quarter of them.
    simulated = cycles * settings.station_takt_seconds[code]
    per_station = settings.micro_stop_mean_interval_seconds * len(
        settings.station_takt_seconds
    )
    assert simulated / len(stops) == pytest.approx(per_station, rel=0.4)


@pytest.mark.asyncio
async def test_a_micro_stop_leaves_the_line_running_and_holds_nothing() -> None:
    """The consequence that separates noise from a §3.3 cause candidate: the line takes
    longer over a cycle and carries on. Nothing is Held, and nothing needs an operator.
    """
    settings = Settings()
    line, clock, nodes = await build_running_line(settings)
    at = clock.history_start
    # Four stations share §3.5's ~20 min line-wide interval, so 4,000 cycles is about
    # 6,000 simulated seconds and five jams -- enough that "none happened" is a failure
    # rather than an unlucky run.
    for _ in range(4_000):
        outcome = await line.step()
        assert outcome is not None
        assert all(state is not State.HELD for state, _ in line.station_states.values())
        at = outcome.at

    jammed = [
        (code, float(value))
        for code, recorded in nodes.items()
        for signal, _, value in recorded.writes
        if signal == "TaktTime"
        and float(value)
        > settings.station_takt_seconds[code] + settings.micro_stop_min_seconds / 2
    ]
    assert jammed, "no micro-stop reached TaktTime in 4,000 cycles"
    assert at > clock.history_start


# --- operator interventions ------------------------------------------------------------


def test_operator_delays_are_inside_their_bounds_and_reproducible() -> None:
    """Task 5's alarms consume these. Here because §3.5 lists operator interventions as
    one of the five things the permanent noise floor is made of, and a delay drawn
    inside the alarm code would be a stream nothing else could reproduce."""
    settings = Settings()
    first = NoiseFloor(settings, settings.seed)
    second = NoiseFloor(settings, settings.seed)
    delays = [first.acknowledge_delay_seconds(i) for i in range(500)]

    assert delays == [second.acknowledge_delay_seconds(i) for i in range(500)]
    assert min(delays) >= settings.operator_ack_min_seconds
    assert max(delays) <= settings.operator_ack_max_seconds
    # Triangular rather than uniform: most acknowledgements are quick and a few wait for
    # a shift change, so the median sits well below the midpoint a uniform draw gives.
    midpoint = (
        settings.operator_ack_min_seconds + settings.operator_ack_max_seconds
    ) / 2
    assert statistics.median(delays) < midpoint

    resets = [first.is_unnecessary_reset(i) for i in range(2_000)]
    assert sum(resets) / len(resets) == pytest.approx(
        settings.operator_unnecessary_reset_rate, abs=0.02
    )
