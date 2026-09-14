"""§3.4a's force-distance curve, and the three knobs M2c's scenarios 3 and 7 turn on."""

from __future__ import annotations

import random
import statistics

import pytest
from simulator.config import Settings
from simulator.curve import contact_of, force_distance, peak_of, work_of

SETTINGS = Settings()
CLAMP = SETTINGS.joining_force_nominal
STIFF = SETTINGS.press_stiffness_nominal
CONTACT = SETTINGS.press_contact_nominal_mm
STROKE = SETTINGS.joining_distance_nominal
STEP = STROKE / (SETTINGS.curve_samples - 1)
SIGMA = SETTINGS.curve_noise_sigma


def rng() -> random.Random:
    """A fresh stream per curve, so two curves in one test differ only where their
    knobs differ and never because of the order they were generated in."""
    return random.Random(20260914)


def curve(
    *,
    contact_mm: float = CONTACT,
    clamp_force: float = CLAMP,
    stiffness: float = STIFF,
    settings: Settings = SETTINGS,
) -> tuple[float, ...]:
    return force_distance(
        rng(),
        settings,
        contact_mm=contact_mm,
        clamp_force=clamp_force,
        stiffness=stiffness,
    )


def test_peak_and_work_are_derived_from_the_curve_not_generated_beside_it() -> None:
    """If they were generated independently they could disagree with the curve that
    is supposed to summarise them, and every scenario-7 query would be reading two
    unrelated numbers."""
    # A trace this module has never produced, built by hand against a 10 mm stroke
    # sampled every millimetre: nothing at all until 4 mm, 1000 N per mm to a 4000 N
    # clamp at 8 mm, clamped to the end. Nothing is stored beside it, so whatever comes
    # back was read out of the array.
    settings = Settings(curve_samples=11, joining_distance_nominal=10.0)
    by_hand = tuple(min(4000.0, max(0.0, 1000.0 * (i - 4))) for i in range(11))

    assert peak_of(by_hand) == 4000.0
    assert contact_of(by_hand, settings) == pytest.approx(4.0)
    # The triangle under the rise plus the clamp held over the last 2 mm.
    assert work_of(by_hand, settings) == pytest.approx(0.5 * 4000 * 4 + 4000 * 2)

    real = curve()
    assert peak_of(real) == max(real)
    assert peak_of(real) in real


def test_a_later_contact_point_leaves_both_published_scalars_alone() -> None:
    """M2c's scenario 7: an undersized component lets the ram travel further before it
    meets resistance. The clamp still caps the load and the ram still runs to the same
    hard stop, so §4.1's two scalars do not move -- the trace is the only place the
    component is visible, which is why §3.4a stores it and why §3.5 calls this the
    strongest test in the set."""
    late = curve(contact_mm=CONTACT + 1.5)
    nominal = curve()

    # PeakForce: the clamp, unmoved. JoiningDistance is the hard stop and is not a
    # function of the curve at all, so there is nothing here for a contact point to
    # move it through.
    assert peak_of(late) == pytest.approx(peak_of(nominal), rel=0.01)

    # The shape, however, moves -- in the samples, in the fitted contact point, and in
    # the work, which is the statistic the two scalars cannot produce.
    differing = sum(a != b for a, b in zip(late, nominal, strict=True))
    assert differing >= 6, "a 1.5 mm shift has to move more than a sample or two"
    assert contact_of(late, SETTINGS) - contact_of(nominal, SETTINGS) == pytest.approx(
        1.5, abs=0.05
    )
    assert work_of(late, SETTINGS) < 0.95 * work_of(nominal, SETTINGS)


def test_a_lower_clamp_force_moves_the_peak_without_moving_the_contact_point() -> None:
    """M2c's scenario 3, and the mirror of the test above. The press's knob and the
    components' knob must be separable in both directions or the analysis cannot tell
    scenario 3 from scenario 7 either."""
    drifted = curve(clamp_force=CLAMP * 0.9)
    nominal = curve()

    assert peak_of(drifted) == pytest.approx(0.9 * peak_of(nominal), rel=0.01)
    assert contact_of(drifted, SETTINGS) == pytest.approx(
        contact_of(nominal, SETTINGS), abs=0.05
    )


def test_the_two_scalars_cannot_reconstruct_the_curve() -> None:
    """The defect this module was rebuilt to remove, kept as a guard.

    A first draft tied the peak to the contact point through a fixed seating depth,
    which made (peak, distance) invertible back into every sample of the trace. D6's
    curve table then stored nothing the scalar table did not, and scenario 7 collapsed
    into a two-scalar query pointing at the right cause. Same published scalars, two
    genuinely different traces, or the table is decorative.
    """
    late = curve(contact_mm=CONTACT + 1.5)
    nominal = curve()

    assert peak_of(late) == pytest.approx(peak_of(nominal), rel=0.01)
    differing = sum(a != b for a, b in zip(late, nominal, strict=True))
    assert differing >= 6
    assert abs(work_of(late, SETTINGS) - work_of(nominal, SETTINGS)) > 0.05 * work_of(
        nominal, SETTINGS
    )


def test_the_trace_is_idle_until_contact_then_rises_to_the_clamp_and_holds() -> None:
    """A force trace that wanders before contact is noise pretending to be physics."""
    contact_mm = 7.0
    stiffness = STIFF
    trace = curve(contact_mm=contact_mm, stiffness=stiffness)
    knee_mm = contact_mm + CLAMP / stiffness
    floor = 6 * SIGMA

    idle = [f for i, f in enumerate(trace) if i * STEP <= contact_mm]
    clamped = [f for i, f in enumerate(trace) if i * STEP >= knee_mm]
    assert len(idle) > 1, "the idle stroke has to be long enough to be a flat part"
    assert all(abs(force) < floor for force in idle), (
        f"the idle stroke left the noise band: {idle}"
    )
    assert all(abs(force - CLAMP) < floor for force in clamped), (
        "past the knee the press holds at its clamp"
    )

    # And in between it climbs at the stiffness it was given.
    rise = [
        (i * STEP, f) for i, f in enumerate(trace) if contact_mm < i * STEP < knee_mm
    ]
    fitted = statistics.linear_regression([x for x, _ in rise], [f for _, f in rise])
    assert fitted.slope == pytest.approx(stiffness, rel=0.02)


def test_the_load_cell_is_noisy_on_the_whole_trace_not_just_the_idle_part() -> None:
    """Zero variance in the rise is what made the first draft exactly invertible, and
    it would leave a part's measured contact point with no spread at all -- which is
    where §3.5 wants the analysis to have to run a significance test rather than read
    one part and declare a lot bad."""
    trace = curve()
    knee_mm = CONTACT + CLAMP / STIFF
    ideal = [
        min(CLAMP, max(0.0, STIFF * (i * STEP - CONTACT))) for i in range(len(trace))
    ]

    residuals = [measured - want for measured, want in zip(trace, ideal, strict=True)]
    rising = [
        r
        for r, i in zip(residuals, range(len(trace)), strict=True)
        if CONTACT < i * STEP < knee_mm
    ]
    clamped = [
        r
        for r, i in zip(residuals, range(len(trace)), strict=True)
        if i * STEP >= knee_mm
    ]
    assert all(r != 0.0 for r in rising), "the rise is noiseless"
    assert all(r != 0.0 for r in clamped), "the clamped tail is noiseless"
    assert statistics.stdev(residuals) == pytest.approx(SIGMA, rel=0.5)


def test_a_curve_makes_the_same_number_of_draws_wherever_contact_falls() -> None:
    """M2c compares a scenario run against a baseline run on the same seed. A contact
    point that also shifted the RNG stream would move signals the scenario never
    touched, and the comparison would be against a different run."""
    early, late = rng(), rng()
    force_distance(early, SETTINGS, contact_mm=6.0, clamp_force=CLAMP, stiffness=STIFF)
    force_distance(late, SETTINGS, contact_mm=9.0, clamp_force=CLAMP, stiffness=STIFF)
    assert early.random() == late.random()


def test_the_same_seed_produces_the_same_curve() -> None:
    """§3.6."""
    assert curve() == curve()


def test_a_press_that_would_reach_its_stop_before_its_clamp_is_refused() -> None:
    """The peak would then be a number the press never commanded -- the contact point
    leaking into PeakForce, which is exactly the confusion between scenarios 3 and 7
    that this module exists to prevent. M2c's scenario 7 moves the contact point, so
    this guard sits on a path a scenario drives."""
    with pytest.raises(ValueError, match="still short of the clamp"):
        curve(contact_mm=STROKE - 1.0)
    with pytest.raises(ValueError, match="still short of the clamp"):
        curve(stiffness=STIFF / 4)


def test_a_fit_band_that_reaches_into_the_noise_is_refused() -> None:
    """Enforced rather than documented: a noise sample fitted as if it were on the rise
    gives a contact point that is wrong by more than scenario 7 moves it, and nothing
    about the result would look wrong."""
    settings = Settings(curve_fit_band_low=0.0001)
    with pytest.raises(ValueError, match="sigma of a"):
        contact_of(curve(settings=settings), settings)


def test_a_curve_too_coarse_to_fit_is_refused_rather_than_guessed() -> None:
    """Two samples on the rise is the minimum a line can be fitted through. Fewer and
    the contact point would be invented."""
    settings = Settings(curve_samples=5)
    coarse = curve(settings=settings)
    with pytest.raises(ValueError, match="band of a"):
        contact_of(coarse, settings)
