"""§3.5's six faults as modifiers on the numbers they change.

These assert what a fault *does to a number*, and -- where the claim is about the plant
rather than the engine -- what a station publishes with one loaded. Nothing here asserts
that a fault was injected: that would be testing the injector, and M2c's recurring
failure mode.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import RecordingNodes
from simulator.alarms import AlarmSystem
from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.faults import (
    CARRIER_ID,
    DEFECT_CLASS,
    DEFECT_CLASSES_BY_KIND,
    DEFECT_PROPENSITY,
    JOINING_CLAMP_FORCE,
    LANE,
    LANE_FILL,
    OUTFEED_FILL,
    QUANTITIES,
    Fault,
    FaultKind,
    FaultSet,
)
from simulator.identity import LANES, LotSchedule, load_carrier
from simulator.inspection_client import DEFECT_CLASSES
from simulator.line import PartState
from simulator.stations import FeedingStation, JoiningStation, OutfeedStation

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
TAKT = timedelta(seconds=Settings().takt_seconds)

HOUR = timedelta(hours=1)


def _drift(at: timedelta = HOUR, until: timedelta | None = None) -> Fault:
    """Scenario 3's joining-force drift, at the configured magnitude."""
    settings = Settings()
    return Fault(
        FaultKind.JOINING_FORCE_DRIFT,
        at,
        {
            "newtons": settings.joining_force_drift_newtons,
            "ramp_seconds": settings.joining_force_drift_ramp_seconds,
        },
        until,
    )


async def _press_peaks(faults: FaultSet, count: int, first_at: datetime) -> list[float]:
    """What S2 publishes as `JoiningForcePeak` over `count` consecutive parts.

    Through the real station, not through `FaultSet.modify` directly: the claim being
    made is about the plant's output, and a fault that was applied to the wrong side of
    the clamp draw would pass every engine-level assertion and still change the run.
    """
    nodes = RecordingNodes("S2_Joining")
    settings = Settings()
    station = JoiningStation(
        nodes,
        settings,
        settings.seed,
        AlarmSystem(settings, settings.seed, {nodes.code: nodes}),
        faults=faults,
    )
    schedule = LotSchedule(settings, first_at)
    for index in range(count):
        at = first_at + index * TAKT
        part = PartState(assembly=load_carrier(schedule, index, index % 4, at))
        await station.run_cycle(at, Carrier(index % 4), part)
    return [
        float(value)
        for signal, _, value in nodes.writes
        if signal == "JoiningForcePeak"
    ]


# --- the identity property -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fault_outside_its_window_changes_nothing() -> None:
    """The identity property. Every fault is a no-op before its offset and after its
    end, so a run with a scenario loaded but not yet fired is byte-identical to a run
    with none -- which is what makes the ground-truth log's timestamps meaningful.

    Asserted on what S2 publishes rather than on `modify`'s return value: the way to
    break this without breaking the engine is to move the fault to the *mean* the clamp
    is drawn from, which shifts the RNG stream and changes every part in the run,
    including the ones before the fault fires.
    """
    clean = await _press_peaks(FaultSet(), 40, T0)
    # Fires an hour in; forty parts at a ~6 s takt is four minutes, so the whole run is
    # before the window.
    loaded = await _press_peaks(FaultSet([_drift(at=HOUR)], T0), 40, T0)
    assert loaded == clean

    # And after the repair. The drift runs for the first hour and is cleared before
    # these parts, so their peaks are the clean ones again rather than a drifted level
    # the fault left behind.
    repaired = FaultSet([_drift(at=timedelta(0), until=timedelta(minutes=2))], T0)
    after = await _press_peaks(repaired, 40, T0 + timedelta(minutes=5))
    assert after == await _press_peaks(FaultSet(), 40, T0 + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_the_drifted_press_reaches_a_lower_peak_than_the_clean_one() -> None:
    """The other half of the identity property: inside its window the fault has to
    actually move the number, or the test above passes on a modifier that does nothing
    at all."""
    clean = await _press_peaks(FaultSet(), 40, T0)
    drifted = await _press_peaks(
        FaultSet([_drift(at=timedelta(0))], T0), 40, T0 + timedelta(hours=2)
    )
    settings = Settings()
    # Two hours in, past the one-hour ramp, so the drift is at its full magnitude.
    assert sum(drifted) / len(drifted) == pytest.approx(
        sum(clean) / len(clean) + settings.joining_force_drift_newtons,
        abs=2 * settings.joining_force_sigma,
    )


# --- composition ---------------------------------------------------------------------


def test_two_faults_on_one_quantity_compose_rather_than_race() -> None:
    """§3.5 allows a scenario to stack. If the second silently replaced the first, a
    scenario script would behave differently depending on declaration order."""
    first = Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"carrier": 7, "factor": 2.0})
    second = Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"carrier": 7, "factor": 3.0})
    context = {CARRIER_ID: 7, LANE: 1, DEFECT_CLASS: "scratch"}

    both = FaultSet([first, second], T0)
    reversed_order = FaultSet([second, first], T0)

    assert both.modify(DEFECT_PROPENSITY, 0.01, T0, **context) == pytest.approx(0.06)
    # Not 0.02 and not 0.03: either would be one fault having replaced the other.
    assert reversed_order.modify(
        DEFECT_PROPENSITY, 0.01, T0, **context
    ) == pytest.approx(0.06)
    assert both.active_at(T0) == (first, second)


def test_only_the_faults_whose_window_contains_the_instant_compose() -> None:
    """The composition above must not quietly become "every fault ever declared"."""
    early = Fault(
        FaultKind.CARRIER_WEAR,
        timedelta(0),
        {"carrier": 7, "factor": 2.0},
        until=timedelta(minutes=30),
    )
    late = Fault(FaultKind.CARRIER_WEAR, HOUR, {"carrier": 7, "factor": 3.0})
    both = FaultSet([early, late], T0)
    context = {CARRIER_ID: 7, LANE: 1, DEFECT_CLASS: "scratch"}

    assert both.modify(DEFECT_PROPENSITY, 0.01, T0, **context) == pytest.approx(0.02)
    at_two_hours = T0 + 2 * HOUR
    assert both.modify(
        DEFECT_PROPENSITY, 0.01, at_two_hours, **context
    ) == pytest.approx(0.03)


# --- context -------------------------------------------------------------------------


def test_carrier_wear_reads_the_carrier_and_lane_contamination_reads_the_lane() -> None:
    """A modifier that ignored its context would apply to every part, which is
    exactly the difference between scenario 4 (one carrier) and a line-wide drift --
    and scenario 4's whole diagnostic value is that it concentrates."""
    wear = FaultSet(
        [Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"carrier": 7, "factor": 4.0})], T0
    )
    worn = wear.modify(
        DEFECT_PROPENSITY, 0.01, T0, carrier_id=7, lane=1, defect_class="scratch"
    )
    other = wear.modify(
        DEFECT_PROPENSITY, 0.01, T0, carrier_id=6, lane=1, defect_class="scratch"
    )
    assert worn == pytest.approx(0.04)
    assert other == 0.01

    contamination = FaultSet(
        [
            Fault(
                FaultKind.LANE_CONTAMINATION,
                timedelta(0),
                {"lane": 2, "factor": Settings().lane_contamination_factor},
            )
        ],
        T0,
    )
    dirty = contamination.modify(
        DEFECT_PROPENSITY,
        0.01,
        T0,
        carrier_id=7,
        lane=2,
        defect_class="contamination",
    )
    clean = contamination.modify(
        DEFECT_PROPENSITY,
        0.01,
        T0,
        carrier_id=7,
        lane=1,
        defect_class="contamination",
    )
    assert dirty > 0.01
    assert clean == 0.01
    # And the carrier the contaminated lane's part happened to ride is untouched by it:
    # the two faults are scoped on different things, which is what keeps scenario 4 and
    # scenario 5 different scenarios.
    assert (
        contamination.modify(
            DEFECT_PROPENSITY,
            0.01,
            T0,
            carrier_id=7,
            lane=1,
            defect_class="scratch",
        )
        == 0.01
    )


def test_each_fault_raises_only_the_defect_classes_3_5_names_for_it() -> None:
    """Scenario 4 is `misalignment` + `scratch`; scenario 5 is `missing_part` +
    `contamination`. A fault that raised every class would make DP-02's class *pair* --
    the thing the pattern is keyed on -- carry no information."""
    wear = FaultSet(
        [Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"carrier": 7, "factor": 4.0})], T0
    )
    raised = {
        name
        for name in DEFECT_CLASSES
        if wear.modify(
            DEFECT_PROPENSITY, 0.01, T0, carrier_id=7, lane=1, defect_class=name
        )
        > 0.01
    }
    assert raised == set(DEFECT_CLASSES_BY_KIND[FaultKind.CARRIER_WEAR])


def test_every_class_a_fault_raises_is_one_the_classifier_scores() -> None:
    """`faults` names §3.5's classes as literals because importing the vocabulary would
    be an import cycle. This is what keeps the two copies from drifting -- a fault
    raising a class no classifier scores would fire, be logged, and concentrate
    nothing."""
    named = {name for classes in DEFECT_CLASSES_BY_KIND.values() for name in classes}
    assert named <= set(DEFECT_CLASSES)


def test_a_propensity_asked_for_without_its_context_is_refused() -> None:
    """Checked on every call, not only when a scoped fault is active: a call site that
    forgot its carrier would otherwise work for every run until scenario 4 fired."""
    with pytest.raises(ValueError, match="carrier_id"):
        FaultSet().modify(DEFECT_PROPENSITY, 0.01, T0, lane=1, defect_class="scratch")


def test_a_quantity_no_fault_modifies_is_refused() -> None:
    """A mistyped quantity is otherwise a modifier that never fires and never says so."""
    with pytest.raises(ValueError, match="joining_force"):
        FaultSet().modify("joining_force", 1.0, T0)
    assert set(QUANTITIES) == {
        LANE_FILL,
        OUTFEED_FILL,
        JOINING_CLAMP_FORCE,
        DEFECT_PROPENSITY,
        "optics_clarity",
        "press_contact",
    }


# --- determinism ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_same_seed_and_scenario_reproduce_the_same_modifications() -> None:
    """§3.6. Two independently built runs of one scenario produce the same numbers, and
    the run that carries the scenario draws from the same RNG stream as the run that
    does not -- which is why nothing in `faults` draws a random number at all."""
    scenario = [
        _drift(at=timedelta(0)),
        Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"carrier": 7, "factor": 2.4}),
    ]
    first = await _press_peaks(FaultSet(scenario, T0), 60, T0 + 2 * HOUR)
    second = await _press_peaks(FaultSet(list(scenario), T0), 60, T0 + 2 * HOUR)
    assert first == second

    # The carrier-wear fault above touches a quantity S2 never asks about, so S2's own
    # output has to be exactly what the drift alone produces. A fault that reached into
    # a shared RNG would break this and nothing else.
    drift_only = await _press_peaks(
        FaultSet([_drift(at=timedelta(0))], T0), 60, T0 + 2 * HOUR
    )
    assert first == drift_only


# --- windows and parameters that cannot do what they say --------------------------


def test_a_window_that_is_repaired_before_it_fires_is_refused() -> None:
    """`until` reads naturally as a duration. Written as one against a non-zero offset
    it produces a fault that is never active for a single instant -- injected, recorded
    in ground truth, and with no consequence anywhere for M2c's proofs to find."""
    with pytest.raises(ValueError, match="offsets from the run origin"):
        Fault(FaultKind.OPTICS_FOULING, 2 * HOUR, {"factor": 0.5}, until=HOUR)


def test_a_parameter_the_fault_does_not_read_is_refused() -> None:
    """The same failure as a config mapping keyed on the wrong spelling: the magnitude
    that was meant to change something changes nothing, silently."""
    with pytest.raises(ValueError, match="ramp_second"):
        Fault(FaultKind.OPTICS_FOULING, HOUR, {"factor": 0.5, "ramp_second": 60.0})


def test_carrier_wear_must_name_the_carrier_it_is_on() -> None:
    """Scenario 4 concentrates. A wear fault free to omit its carrier is a line-wide
    drift wearing scenario 4's name."""
    with pytest.raises(ValueError, match="naming which one"):
        Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"factor": 2.0})
    with pytest.raises(ValueError, match="not a whole number"):
        Fault(FaultKind.CARRIER_WEAR, timedelta(0), {"carrier": 7.5, "factor": 2.0})


def test_faults_declared_with_no_origin_are_refused() -> None:
    """Offsets with nothing to measure from name no instant, so every one of them would
    be permanently outside its own window."""
    with pytest.raises(ValueError, match="no origin"):
        FaultSet([_drift()])


# --- the numbers the plant publishes ---------------------------------------------


@pytest.mark.asyncio
async def test_a_starved_lane_reads_empty_and_the_other_lane_does_not() -> None:
    """Scenario 1's first observable. Asserted on `LaneFill_n` rather than on the fault,
    because the level is what leaves the plant."""
    settings = Settings()
    fault = Fault(
        FaultKind.FEEDER_STARVATION,
        timedelta(0),
        {"lane": 2, "factor": settings.feeder_starvation_factor},
    )
    nodes = RecordingNodes("S1_Feeding")
    station = FeedingStation(
        nodes,
        settings,
        settings.seed,
        LotSchedule(settings, T0),
        faults=FaultSet([fault], T0),
    )
    await station.run_cycle(T0, Carrier(0), PartState())

    levels = {
        signal: float(value)
        for signal, _, value in nodes.writes
        if signal.startswith("LaneFill_")
    }
    assert set(levels) == {f"LaneFill_{lane}" for lane in LANES}
    assert levels["LaneFill_2"] == 0.0
    assert levels["LaneFill_1"] > 0.0


@pytest.mark.asyncio
async def test_a_blocked_outfeed_fills_past_the_level_an_operator_clears_it_at() -> (
    None
):
    """Scenario 2's first observable. `OutfeedFill` normally sawtooths back to zero every
    `outfeed_capacity` parts, because an operator takes the bin away; a blocked outfeed
    is that not happening, and it shows up as a level above the point it wraps at.

    Asserted on the published level rather than on the fault, and past the ramp rather
    than at its start, because the ramp is what makes a blockage build the way a stopped
    discharge conveyor does instead of appearing between two cycles.
    """
    settings = Settings()
    fault = Fault(
        FaultKind.OUTFEED_BLOCKAGE,
        timedelta(0),
        {
            "parts": settings.outfeed_blockage_parts,
            "ramp_seconds": settings.outfeed_blockage_ramp_seconds,
        },
    )
    nodes = RecordingNodes("S4_Outfeed")
    station = OutfeedStation(
        nodes, settings, settings.seed, faults=FaultSet([fault], T0)
    )
    part = PartState(
        assembly=load_carrier(LotSchedule(settings, T0), 0, 0, T0), disposition="good"
    )
    # One cycle at the instant it fires and one an hour later, past the ten-minute ramp.
    await station.run_cycle(T0, Carrier(0), part)
    await station.run_cycle(T0 + HOUR, Carrier(0), part)

    levels = [
        float(value) for signal, _, value in nodes.writes if signal == "OutfeedFill"
    ]
    assert len(levels) == 2
    # At the instant of injection the ramp has not moved, so the plant is still the
    # clean one -- the identity property, at the boundary.
    assert levels[0] < settings.outfeed_capacity
    assert levels[1] > settings.outfeed_capacity
