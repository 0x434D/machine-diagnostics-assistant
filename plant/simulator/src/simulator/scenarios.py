"""§3.5's eight scenarios, declarative: a list of `(offset, fault, params)` each.

**A scenario says what it injects and what should follow, and nothing else.** It does
not diagnose, it does not name a cause, and it does not know what the analysis will
later be asked. The `Consequence` beside each fault is what M2c is allowed to assert --
that a sequence of suspensions appeared, that a class rate rose, that a stream did not
move -- and every one of them is checkable against the plant's own output with no
reasoning in between. M3 is where a cause is inferred; M7 is where the inference is
scored. Writing the expected *cause* here would make every one of M7's numbers circular,
because the log would have been written by the reasoning being graded against it.

**The offsets are simulated-time offsets from the run's origin, never instants.** A
scenario written against a wall clock could be run once. `Scenario.fault_set` places
them on an origin -- the line's `history_start` -- and nothing else in this module knows
what time it is.

**Two of the eight need a fault §3.5's list does not name**, and `faults.FaultKind` has
the account: rows 7 and 8 are component faults, not machine faults, and reusing lane
contamination for them would make scenarios 5 and 7 the same injection.

**What the magnitudes are measured against is in `config`**, beside each one. This
module composes them; it does not hold a number of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from simulator.config import Settings
from simulator.faults import Fault, FaultKind, FaultSet
from simulator.inspection_client import DEFECT_CLASSES, GAP

# Where a consequence is observable. §5.2's table names where the analysis reads them
# back, and a §4.1 signal name where it is a stream -- so a reader of the ground-truth
# log knows which of the two it is being pointed at without a convention to remember.
STATE_CHANGES: Final = "state_changes"
INSPECTION_RESULTS: Final = "inspection_results"
JOINING_FORCE_PEAK: Final = "S2_Joining.JoiningForcePeak"

# What must be true there. Names rather than predicates, because a predicate cannot be
# written to a JSONL log -- Task 7 dispatches on these, and the set is closed so that a
# consequence nothing knows how to check is a failure rather than a silent pass.
SUSPENDED_IN_ORDER: Final = "suspended_in_order"
"""Every named station suspends, in the order given, inside `within_seconds`. The
**order** is the claim: a line with no buffers would suspend all of them at once, and an
assertion that merely counted them would pass on one."""
BLOCKED_IN_ORDER: Final = "blocked_in_order"
"""The same, upstream: the blockage reaches each named station in turn."""
CLASS_RATE_RISES: Final = "class_rate_rises"
"""The named defect classes are more frequent inside the fault's window than before it,
and the classes **not** named are not."""
CLASS_CONCENTRATES: Final = "class_concentrates"
"""The named classes rise on the one carrier in `scope` and not line-wide -- which is the
difference between scenario 4 and a drift, and needs a significance test against the
pool's own spread rather than an `ORDER BY`."""
CONFIDENCE_DECAYS: Final = "confidence_decays"
"""Every named class's score falls, together, while the verdicts do not change."""
SCRAP_RATE_FLAT: Final = "scrap_rate_flat"
"""The reject rate inside the window is the rate outside it. Scenario 6's other half,
and what separates a fouled lens from anything that actually damages parts."""
STREAM_STABLE: Final = "stream_stable"
"""The stream does not move across the window. **Scenario 7's load-bearing assertion**:
its symptom is a rising `gap`, which is exactly what scenario 3's drifting clamp
produces, and the only thing separating them is that here the force does not move."""
STREAM_FALLS: Final = "stream_falls"
"""The stream's level inside the window is below its level before it."""
ONE_PART_ONLY: Final = "one_part_only"
"""Exactly one part carries the named classes because of this fault. Scenario 8, and it
exists to stop one bad component being reported as a bad lot."""


EXPECTATIONS: Final = frozenset(
    {
        SUSPENDED_IN_ORDER,
        BLOCKED_IN_ORDER,
        CLASS_RATE_RISES,
        CLASS_CONCENTRATES,
        CONFIDENCE_DECAYS,
        SCRAP_RATE_FLAT,
        STREAM_STABLE,
        STREAM_FALLS,
        ONE_PART_ONLY,
    }
)
OBSERVABLES: Final = frozenset({STATE_CHANGES, INSPECTION_RESULTS, JOINING_FORCE_PEAK})
SCOPES: Final = frozenset({"carrier"})
"""The three closed sets, enforced in `Consequence.__post_init__`.

Enforced rather than documented: a consequence naming an expectation nothing knows how
to check is a claim in the ground-truth log that every later assertion would skip, which
is a silent pass -- and a scenario is exactly the kind of thing that grows a new
expectation spelled slightly differently. `SCOPES` holds one member and `Consequence.
scope` carries the reason it is not two.
"""


@dataclass(frozen=True)
class Consequence:
    """One thing a fault must make observable, in the form Task 7 asserts it in.

    Every field is JSON, because this is written to the ground-truth log verbatim
    (§3.6) and read back by a test in another milestone.

    Raises ValueError for an `observable`, an `expect` or a `scope` outside the three
    closed sets above.
    """

    observable: str
    """One of the three names above: a §5.2 table, or a `<station>.<signal>` stream."""
    expect: str
    """One of the closed set of expectation names above."""
    subjects: tuple[str, ...] = ()
    """The stations or defect classes it is about, **in the order the claim is made in**
    where the order is part of the claim."""
    scope: str = ""
    """`carrier=7`, or empty for line-wide.

    **It names a partition of the plant's own output, and `carrier` is the only one there
    is.** The carrier id is on every `InspectionResultEvent`, so "these classes rose on
    carrier 7 and not elsewhere" has a contrast group and can be asserted. `lane` is not:
    every assembly draws one component from each lane, so the exposed set for either lane
    is *every part* and a consequence reading "`gap` rose on lane 1" has nothing to
    compare against. It was written that way in the first draft of this file for
    scenarios 5, 7 and 8, and it would have had a later milestone scoring an answer
    against a claim the plant's output cannot support.

    **Which lane a fault is on is still recorded** -- in the injection's own `params`,
    and per defect in the ground-truth log, where `InspectionClient.truth_by_lane` puts
    it. It is a fact about the cause, not an observable, and the two belong in different
    fields.

    A string rather than a pair of optional fields, because it is one fact -- which part
    of the line this is about -- and two nullable fields would make "neither" and "both"
    expressible.
    """
    within_seconds: float | None = None
    """How long the whole consequence may take to appear, where that is part of the
    claim. Derived from the line's own geometry rather than chosen, so that changing the
    buffer capacity or the takt moves it -- see `_propagation_seconds`."""

    def __post_init__(self) -> None:
        if self.observable not in OBSERVABLES:
            raise ValueError(
                f"{self.observable!r} is not somewhere a consequence can be observed; "
                f"the ground-truth log points at {sorted(OBSERVABLES)}"
            )
        if self.expect not in EXPECTATIONS:
            raise ValueError(
                f"{self.expect!r} is not an expectation anything knows how to check; "
                f"§3.5's eight use {sorted(EXPECTATIONS)}. A new one is a new assertion "
                "in Task 7, not a new string here"
            )
        if self.scope:
            key, _, value = self.scope.partition("=")
            if key not in SCOPES or not value.isdigit():
                raise ValueError(
                    f"{self.scope!r} is not a partition of the plant's output; the only "
                    f"one is {sorted(SCOPES)} (as `carrier=7`). `lane=2` was here and is "
                    "the case this refuses: every assembly draws from both lanes, so a "
                    "lane has no contrast group and the consequence cannot be checked "
                    "against anything. Which lane a fault is on belongs in the fault's "
                    "params and in the ground-truth log's per-defect lane"
                )


@dataclass(frozen=True)
class Injection:
    """One fault and what it is expected to produce.

    The pair is the unit, because §3.6 puts them in the log together: a fault with no
    expected consequence is an injection nothing can be asserted about, and a
    consequence with no fault is an expectation about the noise floor.
    """

    fault: Fault
    consequences: tuple[Consequence, ...]


@dataclass(frozen=True)
class Scenario:
    """§3.5's row `number`, as what it injects and what should follow."""

    number: int
    name: str
    injections: tuple[Injection, ...]
    note: str = ""
    """What a reader of the log has to know that the fields do not say -- scenario 6's
    stipulation, scenario 5's lane attribution. Empty where there is nothing to add."""

    @property
    def faults(self) -> tuple[Fault, ...]:
        return tuple(injection.fault for injection in self.injections)

    def fault_set(self, origin: datetime) -> FaultSet:
        """This scenario's faults, placed on the run that starts at `origin`."""
        return FaultSet(self.faults, origin)


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


def _propagation_seconds(settings: Settings) -> float:
    """How long a stoppage at one end of the line takes to reach the other.

    One buffer's worth of parts has to be consumed before the station below it starves,
    at the line's takt, and there are one fewer buffers than stations -- 90 s at the
    shipped values. Doubled as a **margin**, not as a second measurement: §3.5's
    micro-stops add up to `micro_stop_max_seconds` to any cycle and a station can take
    several while a chain propagates, so the claim has to hold on an unlucky run too. The
    chain measured at the shipped settings completes in 45.6 s against the 180 s this
    returns, which is the size of the margin rather than a prediction of it.

    Derived from the line's own geometry because `buffer_capacity` is what §3.1 says sets
    this delay -- a hard 180 s here would be a second, silent copy of three settings, and
    it would go on claiming 180 s after the buffers were made twice as deep.
    """
    stations = len(settings.station_takt_seconds)
    return 2.0 * (stations - 1) * settings.buffer_capacity * settings.takt_seconds


def _lot_window(settings: Settings, index: int) -> tuple[timedelta, timedelta]:
    """When lane production is drawing from its `index`-th lot, as offsets.

    A lot lasts `lot_size` components and every assembly draws one from each lane, so a
    lot is `lot_size` parts wide on both lanes at once, at the line's takt.

    **Approximate, and it has to be.** The boundary moves with the jitter and the
    micro-stops the run actually takes, which is not knowable when the scenario is
    built; `test_scenarios` measures the overlap between this window and the lot the run
    really drew from. The alternative -- naming the lot by its code -- is worse and
    `identity` says why: the codes are seed-derived, a lot that has not loaded yet has no
    code to name, and §3.5's literal `L-4471` is never issued at the shipped seed at all.
    """
    width = settings.lot_size * settings.takt_seconds
    return _seconds(index * width), _seconds((index + 1) * width)


def _feeder_starvation(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds
    return Scenario(
        1,
        "feeder starvation upstream of S1",
        (
            Injection(
                Fault(
                    FaultKind.FEEDER_STARVATION,
                    _seconds(start),
                    # No lane: §3.5 puts this upstream of S1, so both lanes go. The
                    # parameter exists because the same fault can take out one lane, and
                    # that is a different scenario with a different chain.
                    {"factor": settings.feeder_starvation_factor},
                    _seconds(start + settings.feeder_starvation_seconds),
                ),
                (
                    Consequence(
                        STATE_CHANGES,
                        SUSPENDED_IN_ORDER,
                        ("S2_Joining", "S3_Inspection", "S4_Outfeed"),
                        within_seconds=_propagation_seconds(settings),
                    ),
                ),
            ),
        ),
        note=(
            "S1 itself suspends on `starved:feeder`, which is neither a buffer nor the "
            "carrier pool: the consequence below is about the three stations the "
            "emptiness reaches through the buffers, in the order the buffers drain."
        ),
    )


def _outfeed_blockage(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds
    return Scenario(
        2,
        "outfeed blocked after S4",
        (
            Injection(
                Fault(
                    FaultKind.OUTFEED_BLOCKAGE,
                    _seconds(start),
                    {
                        "parts": settings.outfeed_blockage_parts,
                        "ramp_seconds": settings.outfeed_blockage_ramp_seconds,
                    },
                    _seconds(start + settings.outfeed_blockage_seconds),
                ),
                (
                    Consequence(
                        STATE_CHANGES,
                        BLOCKED_IN_ORDER,
                        ("S3_Inspection", "S2_Joining", "S1_Feeding"),
                        within_seconds=_propagation_seconds(settings),
                    ),
                ),
            ),
        ),
        note=(
            "The mirror of scenario 1 and the reason both are in the set: the same "
            "three buffers carry the condition the other way, and a test that asserted "
            "only that every station stopped would pass on a line with no buffers."
        ),
    )


def _joining_force_drift(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds
    return Scenario(
        3,
        "joining force drifts down at S2",
        (
            Injection(
                Fault(
                    FaultKind.JOINING_FORCE_DRIFT,
                    _seconds(start),
                    {
                        "newtons": settings.joining_force_drift_newtons,
                        "ramp_seconds": settings.joining_force_drift_ramp_seconds,
                    },
                ),
                (
                    Consequence(JOINING_FORCE_PEAK, STREAM_FALLS),
                    Consequence(INSPECTION_RESULTS, CLASS_RATE_RISES, (GAP,)),
                ),
            ),
        ),
        note=(
            "Not repaired: a relief valve that has drifted stays drifted until someone "
            "turns it back, and §3.5's row 3 ends in S2 aborting rather than in the "
            "fault clearing itself."
        ),
    )


def _carrier_wear(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds
    carrier = settings.worn_carrier_id
    return Scenario(
        4,
        "carrier 7 wears",
        (
            Injection(
                Fault(
                    FaultKind.CARRIER_WEAR,
                    _seconds(start),
                    {
                        "carrier": float(carrier),
                        "factor": settings.carrier_wear_factor,
                    },
                ),
                (
                    Consequence(
                        INSPECTION_RESULTS,
                        CLASS_CONCENTRATES,
                        ("misalignment", "scratch"),
                        scope=f"carrier={carrier}",
                    ),
                ),
            ),
        ),
        note=(
            "Both classes rise on the carrier, not both on one part: at the shipped "
            "ratio P(both on one part) is 5e-5, and the factor that would change that "
            "puts this carrier at a 19 % scrap rate -- a `GROUP BY` with no significance "
            "test needed, which is the opposite of what §3.5 wants this row to require. "
            "The line's own rate barely moves, so there is no stop."
        ),
    )


def _lane_contamination(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds
    lane = settings.contaminated_lane
    return Scenario(
        5,
        "feeder lane 2 contaminated",
        (
            Injection(
                Fault(
                    FaultKind.LANE_CONTAMINATION,
                    _seconds(start),
                    {
                        "lane": float(lane),
                        "factor": settings.lane_contamination_factor,
                    },
                ),
                (
                    Consequence(
                        INSPECTION_RESULTS,
                        CLASS_RATE_RISES,
                        ("missing_part", "contamination"),
                    ),
                ),
            ),
        ),
        note=(
            "The two classes rise and the other four do not, which is what separates "
            "this from a line-wide rise, and it is the whole of what the plant's output "
            "supports. **Attributing them to lane 2 from a part record alone is not "
            "possible**: every assembly draws one component from each lane, so every "
            "part contains lane 2 and there is no contrast group. The lane is recorded "
            "in this fault's params and against every defect in the ground-truth log "
            "(`InspectionClient.truth_by_lane`), so the answer is scoreable even though "
            "it is not derivable -- which is the difference between a hard question and "
            "an unanswerable one."
        ),
    )


def _optics_fouling(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds
    return Scenario(
        6,
        "optics fouling at S3",
        (
            Injection(
                Fault(
                    FaultKind.OPTICS_FOULING,
                    _seconds(start),
                    {
                        "factor": settings.optics_fouling_factor,
                        "ramp_seconds": settings.optics_fouling_ramp_seconds,
                    },
                ),
                (
                    Consequence(
                        INSPECTION_RESULTS,
                        CONFIDENCE_DECAYS,
                        tuple(DEFECT_CLASSES),
                    ),
                    Consequence(INSPECTION_RESULTS, SCRAP_RATE_FLAT),
                ),
            ),
        ),
        note=(
            "D8: the decay is caused rather than declared -- the renderer veils the "
            "frame and the classifier reads its confidence off the pixels. The honest "
            "limit stays: contrast -> clarity -> confidence is still a formula, one "
            "layer below the knob it replaces rather than nowhere."
        ),
    )


def _bad_lot(settings: Settings) -> Scenario:
    at, until = _lot_window(settings, settings.bad_lot_index)
    lane = settings.bad_lot_lane
    return Scenario(
        7,
        "lane 1 gets a bad lot",
        (
            Injection(
                Fault(
                    FaultKind.UNDERSIZED_COMPONENTS,
                    at,
                    {
                        "lane": float(lane),
                        "millimetres": settings.undersized_component_mm,
                    },
                    until,
                ),
                (
                    Consequence(INSPECTION_RESULTS, CLASS_RATE_RISES, (GAP,)),
                    # The assertion the scenario exists for. Its symptom is scenario 3's
                    # symptom; the force is what tells them apart, and a scenario that
                    # also drifted the force would have quietly become scenario 3.
                    Consequence(JOINING_FORCE_PEAK, STREAM_STABLE),
                ),
            ),
        ),
        note=(
            "The window is the lot's window at the line's nominal takt, which the run's "
            "own jitter and micro-stops move by a little; the lot is named by lane and "
            "index rather than by code, because a lot that has not loaded yet has no "
            "code and §3.5's literal `L-4471` is never issued at the shipped seed. The "
            "two lanes deplete their lots at different parts (`lot_stagger_fraction`), "
            "so this lot covers a part set no lot on the other lane covers -- without "
            "that, the defects would correlate with both lanes' lots equally and the "
            "lot would carry no more information than the period."
        ),
    )


def _defective_component(settings: Settings) -> Scenario:
    start = settings.scenario_warmup_seconds + settings.defective_component_offset
    lane = settings.bad_lot_lane
    return Scenario(
        8,
        "one defective component",
        (
            Injection(
                Fault(
                    FaultKind.UNDERSIZED_COMPONENTS,
                    _seconds(start),
                    {
                        "lane": float(lane),
                        "millimetres": settings.defective_component_mm,
                    },
                    # Shorter than the fastest station's takt, so at most one press
                    # happens inside it -- one component, which is the whole of row 8.
                    _seconds(start + min(settings.station_takt_seconds.values())),
                ),
                (Consequence(INSPECTION_RESULTS, ONE_PART_ONLY, (GAP,)),),
            ),
        ),
        note=(
            "Scenario 7's mirror, and the reason it is in the set: the same fault on "
            "one component instead of five hundred must read as one bad part rather "
            "than as a lot problem."
        ),
    )


_BUILDERS: Final = (
    _feeder_starvation,
    _outfeed_blockage,
    _joining_force_drift,
    _carrier_wear,
    _lane_contamination,
    _optics_fouling,
    _bad_lot,
    _defective_component,
)
"""§3.5's eight, in its own order, which is what `number` reports.

A tuple of builders rather than a table of data: four of the eight derive an offset or a
magnitude from several settings at once, and a table would either hold the derivations
as literals -- the copies §10.3 exists to prevent -- or need a column for each.
"""


def all_scenarios(settings: Settings) -> tuple[Scenario, ...]:
    """§3.5's eight, built against `settings`. In table order, so index + 1 is the row."""
    return tuple(build(settings) for build in _BUILDERS)


def scenario(number: int, settings: Settings) -> Scenario:
    """§3.5's row `number`, counting from 1.

    Raises ValueError for a row §3.5 does not have -- including 0, which reads as "no
    scenario" and is a different thing from a scenario (see `Settings.scenario`).
    """
    if not 1 <= number <= len(_BUILDERS):
        raise ValueError(
            f"§3.5 has scenarios 1-{len(_BUILDERS)}, not {number!r}: 0 means no scenario "
            "and is handled where a run is built, not here"
        )
    return _BUILDERS[number - 1](settings)


__all__ = [
    "BLOCKED_IN_ORDER",
    "CLASS_CONCENTRATES",
    "CLASS_RATE_RISES",
    "CONFIDENCE_DECAYS",
    "INSPECTION_RESULTS",
    "JOINING_FORCE_PEAK",
    "ONE_PART_ONLY",
    "SCRAP_RATE_FLAT",
    "STATE_CHANGES",
    "STREAM_FALLS",
    "STREAM_STABLE",
    "SUSPENDED_IN_ORDER",
    "Consequence",
    "Injection",
    "Scenario",
    "all_scenarios",
    "scenario",
]
