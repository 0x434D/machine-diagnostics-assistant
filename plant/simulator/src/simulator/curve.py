"""§3.4a's force-distance curve: the trace one press leaves against one serial.

**Three knobs, and only one of them reaches a published scalar.** The press is force
-clamped at the top and position-stopped at the bottom: hydraulic relief caps the load,
a hard mechanical stop ends the stroke, and the ram runs to that stop on every part. So

* the **clamp force** sets `JoiningForcePeak` -- the press's knob, and M2c's scenario 3
  drifts it;
* the **stop** sets `JoiningDistance` -- it is not in this module at all, because
  nothing that happens at constant ram position can appear in a force-against-position
  trace. The end of the stroke is where the trace ends, not a feature inside it;
* the **contact point** sets where the load picks up -- the components' knob, moved by
  M2c's scenario 7, and it moves *neither* published scalar. It is visible only in the
  shape of the trace;
* the **stiffness** is the slope of the rise -- the material's, and curve-only.

That is the whole reason §3.4a stores a curve. An earlier draft of this module made the
peak `stiffness x a fixed seating depth`, which made `(peak, distance)` and
`(stiffness, contact)` a bijection: every sample was reconstructible from the two
scalars to ~1e-12 N, D6's curve table held nothing the scalar table did not, and
scenario 7 became a two-scalar query -- rising distance, steady force -- pointing
straight at the *right* cause. §3.5 calls scenario 7 the strongest test in the set
precisely because its symptom points at the wrong one, so that draft destroyed it. A
summary is a projection, and losing the contact point into the curve is what the
projection is for.

Sampled uniformly across the ram's stroke, which is `settings.joining_distance_nominal`
-- the stop. The array carries forces only, so a consumer needs that stroke to turn a
sample index into millimetres; it is published as a static OPC UA node
(`Line/Press/StrokeLength`) rather than copied into the gateway and the analysis
service, because only OPC UA crosses between the stacks and three private copies of one
number is three chances to mis-scale every curve downstream with no error anywhere.

`peak_of`, `contact_of` and `work_of` read the array they are given. Joining work
(N.mm) is the statistic the two scalars cannot produce: a later contact point leaves
both of them where they were and takes a measurable bite out of the area.
"""

from __future__ import annotations

import random
import statistics

from simulator.config import Settings


def force_distance(
    rng: random.Random,
    settings: Settings,
    *,
    contact_mm: float,
    clamp_force: float,
    stiffness: float,
) -> tuple[float, ...]:
    """One part's trace, in newtons, sampled uniformly across the ram's stroke.

    `contact_mm` is where the ram meets resistance (the components' knob), `stiffness`
    is newtons per millimetre of compression after that (the material's), and
    `clamp_force` is where the press stops adding load (the press's own).

    Raises ValueError if the geometry cannot produce a curve: a non-positive clamp,
    stiffness or sample count, a negative contact point, or a part so late or so soft
    that the ram would reach its stop before the clamp ever took over. That last one is
    refused rather than clipped -- the peak would then be a number the press never
    commanded, which is the contact point leaking into `JoiningForcePeak` and the exact
    confusion between scenarios 3 and 7 that this module exists to prevent.
    """
    samples = settings.curve_samples
    stroke = settings.joining_distance_nominal
    if samples < 2:
        raise ValueError(f"a curve needs at least two samples, got {samples!r}")
    if stiffness <= 0:
        raise ValueError(f"stiffness must be positive, got {stiffness!r} N/mm")
    if clamp_force <= 0:
        raise ValueError(f"clamp_force must be positive, got {clamp_force!r} N")
    if contact_mm < 0:
        raise ValueError(f"contact_mm must not be negative, got {contact_mm!r}")

    knee_mm = contact_mm + clamp_force / stiffness
    if knee_mm >= stroke:
        raise ValueError(
            f"a part met at {contact_mm!r} mm and compressing at {stiffness!r} N/mm "
            f"reaches the {clamp_force!r} N clamp at {knee_mm:.3f} mm, at or past the "
            f"{stroke!r} mm stop: the ram would end its stroke still short of the "
            "clamp, and the peak in the trace would be one the press never commanded"
        )

    step = stroke / (samples - 1)
    return tuple(
        min(clamp_force, max(0.0, stiffness * (i * step - contact_mm)))
        + rng.gauss(0.0, settings.curve_noise_sigma)
        for i in range(samples)
    )


def peak_of(curve: tuple[float, ...]) -> float:
    """The largest force in the trace -- §4.1's `JoiningForcePeak`.

    The clamp plus whatever the load cell made of it, which is why it is read off the
    trace rather than handed back from the draw that set the clamp.
    """
    if not curve:
        raise ValueError("an empty curve has no peak")
    return max(curve)


def contact_of(curve: tuple[float, ...], settings: Settings) -> float:
    """Where the load picked up, in millimetres of ram travel.

    Back-extrapolated from the straight part of the rise rather than read off the first
    sample above the noise, so it is continuous in the contact point instead of
    quantised to the sample spacing, and so the load cell's noise averages out across
    the fitted points instead of landing on one of them. This is the only reading that
    moves under M2c's scenario 7.
    """
    slope, intercept = _fit_rise(curve, settings)
    return -intercept / slope


def work_of(curve: tuple[float, ...], settings: Settings) -> float:
    """Joining work, N.mm: the area under the trace across the whole stroke.

    The statistic the two published scalars cannot produce. A part that met the ram
    later is clamped for less of its stroke, so the area falls while the peak and the
    distance both stay exactly where they were -- which is scenario 7's signal, and the
    reason §3.4a keeps the curve rather than two numbers off it.

    Trapezoidal, because the trace is a sampled continuous quantity rather than a
    sequence of independent readings.
    """
    if len(curve) < 2:
        raise ValueError(f"work needs at least two samples, got {len(curve)}")
    step = settings.joining_distance_nominal / (len(curve) - 1)
    return step * (sum(curve) - (curve[0] + curve[-1]) / 2)


def _fit_rise(curve: tuple[float, ...], settings: Settings) -> tuple[float, float]:
    """Slope (N/mm) and intercept (N) of the straight part of the rise.

    Raises ValueError when the configured band reaches down into the load cell's noise,
    and when fewer than two samples fall inside it. Both are refused rather than
    reported, because both produce a plausible wrong number instead of an exception:
    a noise sample fitted as if it were on the rise moves the contact point by more
    than M2c's scenario 7 does.
    """
    peak = peak_of(curve)
    low = settings.curve_fit_band_low * peak
    high = settings.curve_fit_band_high * peak
    margin = settings.curve_fit_noise_margin_sigmas * settings.curve_noise_sigma
    if low < margin:
        raise ValueError(
            f"the fit band starts at {low:.1f} N, inside "
            f"{settings.curve_fit_noise_margin_sigmas!r} sigma of a "
            f"{settings.curve_noise_sigma!r} N noise floor: a sample off the idle "
            "stroke could be fitted as if it were on the rise"
        )

    step = settings.joining_distance_nominal / (len(curve) - 1)
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
