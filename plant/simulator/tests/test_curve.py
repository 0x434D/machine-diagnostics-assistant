"""§3.4a's force-distance curve, and the two knobs M2c's scenario 7 turns on."""

from __future__ import annotations

import itertools
import random

import pytest
from simulator.config import Settings
from simulator.curve import contact_of, distance_of, force_distance, peak_of

SETTINGS = Settings()
NOMINAL = SETTINGS.press_stiffness_nominal
CONTACT = SETTINGS.press_contact_nominal_mm


def rng() -> random.Random:
    """A fresh stream per curve, so a test never depends on the order its curves were
    generated in."""
    return random.Random(20260914)


def curve(
    *,
    contact_mm: float = CONTACT,
    stiffness: float = NOMINAL,
    settings: Settings = SETTINGS,
) -> tuple[float, ...]:
    return force_distance(rng(), settings, contact_mm=contact_mm, stiffness=stiffness)


def test_peak_and_distance_are_derived_from_the_curve_not_generated_beside_it() -> None:
    """If they were generated independently they could disagree with the curve that
    is supposed to summarise them, and every scenario-7 query would be reading two
    unrelated numbers."""
    # A trace this function has never seen, built by hand: 0 N until 5 mm, then 100 N
    # per mm to a 400 N peak at 9 mm, then held. Nothing is stored alongside it, so
    # whatever comes back was read out of the array.
    settings = Settings(curve_samples=21, curve_stroke_mm=20.0)
    step = settings.curve_stroke_mm / (settings.curve_samples - 1)
    by_hand = tuple(min(400.0, max(0.0, 100.0 * (i * step - 5.0))) for i in range(21))

    assert peak_of(by_hand) == 400.0
    assert contact_of(by_hand, settings) == pytest.approx(5.0)
    assert distance_of(by_hand, settings) == pytest.approx(9.0)

    # And on a generated curve the peak is a value that is actually in it.
    real = curve()
    assert peak_of(real) == max(real)
    assert peak_of(real) in real


def test_a_later_contact_point_moves_the_curve_without_moving_the_peak() -> None:
    """The scenario-7 signal: an undersized component lets the press travel further
    before resistance, and the press still reaches its target force."""
    late = curve(contact_mm=8.0)
    early = curve(contact_mm=6.0)

    assert peak_of(late) == pytest.approx(peak_of(early), rel=0.02)
    assert contact_of(late, SETTINGS) > contact_of(early, SETTINGS)
    assert contact_of(late, SETTINGS) == pytest.approx(8.0)
    assert contact_of(early, SETTINGS) == pytest.approx(6.0)
    # And the travel carries the shift, one for one -- §4.1's JoiningDistance is where
    # a contact point that moved 2 mm becomes visible as a scalar.
    assert distance_of(late, SETTINGS) - distance_of(early, SETTINGS) == pytest.approx(
        2.0
    )


def test_a_softer_press_moves_the_peak_without_moving_the_contact_point() -> None:
    """The scenario-3 signal, and the mirror of the test above. The two knobs must be
    separable in both directions or the analysis cannot tell them apart either."""
    soft = curve(stiffness=NOMINAL * 0.9)
    nominal = curve()

    assert peak_of(soft) == pytest.approx(0.9 * peak_of(nominal))
    assert contact_of(soft, SETTINGS) == pytest.approx(contact_of(nominal, SETTINGS))
    # The press still seats to the same depth, so the travel does not move either: a
    # drift shows in the force and nowhere else.
    assert distance_of(soft, SETTINGS) == pytest.approx(distance_of(nominal, SETTINGS))


def test_a_bad_lot_and_a_drifting_press_are_told_apart_by_the_curve() -> None:
    """§3.5 calls scenario 7 the strongest test in the set because the symptom points
    at the wrong cause. This is the separation the analysis has to make: the pair
    (contact point, peak) moves in one coordinate for a bad lot and the other for a
    press drift, and neither scalar alone says which happened."""
    nominal = curve()
    bad_lot = curve(contact_mm=CONTACT + 1.5)
    drifting = curve(stiffness=NOMINAL * 0.92)

    assert peak_of(bad_lot) == pytest.approx(peak_of(nominal))
    assert contact_of(bad_lot, SETTINGS) != pytest.approx(contact_of(nominal, SETTINGS))

    assert peak_of(drifting) != pytest.approx(peak_of(nominal))
    assert contact_of(drifting, SETTINGS) == pytest.approx(
        contact_of(nominal, SETTINGS)
    )

    assert peak_of(bad_lot) != pytest.approx(peak_of(drifting))
    assert contact_of(bad_lot, SETTINGS) != pytest.approx(
        contact_of(drifting, SETTINGS)
    )


def test_the_curve_is_flat_until_contact_and_rises_after() -> None:
    """A force trace that wanders before contact is noise pretending to be physics."""
    contact_mm = 7.0
    trace = curve(contact_mm=contact_mm)
    step = SETTINGS.curve_stroke_mm / (SETTINGS.curve_samples - 1)
    floor = 6 * SETTINGS.curve_noise_sigma

    before = [f for i, f in enumerate(trace) if i * step <= contact_mm]
    after = [f for i, f in enumerate(trace) if i * step > contact_mm]
    assert len(before) > 1, "the flat part has to be long enough to be a flat part"
    assert all(abs(force) < floor for force in before), (
        f"the idle stroke left the noise band: {before}"
    )

    # Past contact the press only ever loads: it climbs to its peak and holds there
    # while the window runs out.
    assert all(later >= earlier for earlier, later in itertools.pairwise(after))
    assert after[0] > max(before)
    assert max(after) == peak_of(trace)


def test_a_curve_makes_the_same_number_of_draws_wherever_contact_falls() -> None:
    """M2c compares a scenario run against a baseline run on the same seed. A contact
    point that also shifted the RNG stream would move signals the scenario never
    touched, and the comparison would be against a different run."""
    early, late = rng(), rng()
    force_distance(early, SETTINGS, contact_mm=6.0, stiffness=NOMINAL)
    force_distance(late, SETTINGS, contact_mm=9.0, stiffness=NOMINAL)
    assert early.random() == late.random()


def test_the_same_seed_produces_the_same_curve() -> None:
    """§3.6. The noise floor is the only drawn part of the trace, so this is the whole
    of what a seed has to pin here."""
    assert curve() == curve()


def test_a_press_that_would_still_be_seating_at_the_end_is_refused() -> None:
    """Clipping would report a peak the press never reached, and M2c's scenario 7
    moves the contact point -- so the guard is on the path a scenario drives."""
    with pytest.raises(ValueError, match="recorded window"):
        curve(contact_mm=SETTINGS.curve_stroke_mm - SETTINGS.press_seat_depth_mm + 0.1)


def test_a_curve_too_coarse_to_fit_is_refused_rather_than_guessed() -> None:
    """Two samples on the rise is the minimum a line can be fitted through. Fewer and
    the summaries would be invented, which is the failure this module exists to
    prevent."""
    settings = Settings(curve_samples=4, curve_stroke_mm=20.0)
    coarse = force_distance(rng(), settings, contact_mm=CONTACT, stiffness=NOMINAL)
    with pytest.raises(ValueError, match="band of a"):
        distance_of(coarse, settings)
