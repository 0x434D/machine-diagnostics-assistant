"""§3.4a's force-distance curve: the trace one press leaves against one serial.

**Two knobs, and they move independently.** *Where* the force begins to rise is about
the incoming components -- an undersized component lets the press travel further before
it meets resistance -- while *how* the load develops after contact is about the press.
M2c's scenario 7 is a bad component lot with a perfectly stable joining force and
scenario 3 is a force drift; both raise `gap` defects, so the curve's shape is the only
thing that separates them. Generate them from one knob and scenario 7 is unwinnable.

The model that makes both true, and why it is the only one of the obvious three:

* The press descends through a fixed recorded window, `settings.curve_stroke_mm` wide,
  sampled uniformly. Below the contact point it is touching nothing and the trace is
  the sensor's noise floor.
* From contact it seats the part a fixed depth (`settings.press_seat_depth_mm`),
  loading it at `stiffness` newtons per millimetre, then holds. So the peak is
  `stiffness x seat_depth` -- it does not know where contact happened.
* A press that instead always stopped at the same absolute position would lower its
  peak whenever contact came late, which is scenario 3's symptom appearing inside
  scenario 7. A press that instead drove to a target force would ignore `stiffness`
  altogether, and scenario 3 would have no signal.

The consequence, stated because it is a real one: the total travel *does* move with the
contact point, so §4.1's `JoiningDistance` shifts under scenario 7 while `PeakForce`
does not. Two independent knobs cannot leave both summaries fixed -- something has to
carry the contact point -- and §3.4a's own account of scenario 7 is that the press
travels further, so the distance is where it shows.

`peak_of`, `contact_of` and `distance_of` read the array they are given. They are not
handed the numbers the curve was generated from: a summary generated beside its curve
can disagree with it, and every scenario-7 query would then be reading two unrelated
numbers.
"""

from __future__ import annotations

import random
import statistics

from simulator.config import Settings


def force_distance(
    rng: random.Random, settings: Settings, *, contact_mm: float, stiffness: float
) -> tuple[float, ...]:
    """One part's trace, in newtons, sampled uniformly across the recorded window.

    `contact_mm` is where the press meets resistance and `stiffness` is newtons per
    millimetre of travel past it -- §3.4a's two knobs, per part, in that order of
    causation (the components decide the first, the press the second).

    Raises ValueError if the geometry cannot produce a curve: a non-positive stiffness,
    a negative contact point, fewer than two samples, or a contact point so late that
    the press would still be seating when the window ends. That last one is refused
    rather than clipped, because a clipped curve reports a peak the press never reached.
    """
    samples = settings.curve_samples
    stroke = settings.curve_stroke_mm
    seat = settings.press_seat_depth_mm
    if samples < 2:
        raise ValueError(f"a curve needs at least two samples, got {samples!r}")
    if stiffness <= 0:
        raise ValueError(f"stiffness must be positive, got {stiffness!r} N/mm")
    if contact_mm < 0:
        raise ValueError(f"contact_mm must not be negative, got {contact_mm!r}")
    if contact_mm + seat > stroke:
        raise ValueError(
            f"a contact point at {contact_mm!r} mm plus a seat depth of {seat!r} mm "
            f"runs past the {stroke!r} mm recorded window: the press would still be "
            "seating when the trace ends, and the peak in it would be one the press "
            "never reached"
        )

    peak = stiffness * seat
    step = stroke / (samples - 1)
    # Drawn for every sample and used only below contact, so the number of draws a part
    # makes does not depend on where its contact point fell. M2c compares a scenario run
    # against a baseline run on the same seed; a shifted contact point that also shifted
    # the RNG stream would move signals the scenario never touched.
    noise = [rng.gauss(0.0, settings.curve_noise_sigma) for _ in range(samples)]
    return tuple(
        noise[i]
        if i * step <= contact_mm
        else min(peak, stiffness * (i * step - contact_mm))
        for i in range(samples)
    )


def peak_of(curve: tuple[float, ...]) -> float:
    """The largest force in the trace -- §4.1's `JoiningForcePeak`."""
    if not curve:
        raise ValueError("an empty curve has no peak")
    return max(curve)


def contact_of(curve: tuple[float, ...], settings: Settings) -> float:
    """Where the force began to rise, in millimetres from the start of the window.

    Back-extrapolated from the straight part of the rise rather than read off the first
    sample above the noise, so it is continuous in the contact point instead of
    quantised to the sample spacing -- a contact shift smaller than one sample is still
    a contact shift, and M2c's scenario 7 is scored on exactly that sensitivity.
    """
    slope, intercept = _fit_rise(curve, settings)
    return -intercept / slope


def distance_of(curve: tuple[float, ...], settings: Settings) -> float:
    """Total travel to the end of the press stroke -- §4.1's `JoiningDistance`.

    Derived from the same fitted rise as `contact_of` and from the curve's own peak, so
    the three summaries are always three readings of one trace.
    """
    slope, intercept = _fit_rise(curve, settings)
    return (peak_of(curve) - intercept) / slope


def _fit_rise(curve: tuple[float, ...], settings: Settings) -> tuple[float, float]:
    """Slope (N/mm) and intercept (N) of the straight part of the rise.

    Raises ValueError when fewer than two samples fall inside the configured band --
    a curve too coarsely sampled, or one with no rise in it at all.
    """
    peak = peak_of(curve)
    low = settings.curve_fit_band_low * peak
    high = settings.curve_fit_band_high * peak
    step = settings.curve_stroke_mm / (len(curve) - 1)
    band = [(i * step, force) for i, force in enumerate(curve) if low <= force <= high]
    if len(band) < 2:
        raise ValueError(
            f"only {len(band)} of {len(curve)} samples fall in the "
            f"{settings.curve_fit_band_low:.0%}-{settings.curve_fit_band_high:.0%} "
            f"band of a {peak:.1f} N peak, and a line needs two: either the rise is "
            "sampled too coarsely for this band or this is not a press curve"
        )
    fit = statistics.linear_regression([x for x, _ in band], [f for _, f in band])
    if fit.slope <= 0:
        raise ValueError(f"a press curve rises; this one fits a slope of {fit.slope!r}")
    return fit.slope, fit.intercept
