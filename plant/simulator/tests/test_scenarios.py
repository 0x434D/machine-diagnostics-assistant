"""§3.5's eight scenarios, asserted by their consequences and by nothing else.

**Nothing here diagnoses anything.** Every assertion below is of the form "this appeared
on the line" -- a sequence of suspensions, a class rate that rose, a stream that did not
move. None of them says what an analysis should conclude, and none of them could: that
is M3, and scoring it is M7. The specific hazard this milestone keeps hitting is a test
that asserts a fault was *injected*, which tests the injector and not the plant, so each
test below reads the plant's own output and never the `FaultSet`.

The scenarios are read from `simulator.scenarios` rather than rebuilt here, including
the tolerances: what a scenario claims it will produce is what goes in the ground-truth
log (§3.6), so a claim this file weakened would be a claim the log still makes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import pytest
from conftest import RecordingNodes, build_running_line, new_clock
from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.faults import NO_FAULTS, FaultSet
from simulator.identity import LANES
from simulator.inspection_client import DEFECT_CLASSES, GAP, InspectionClient
from simulator.packml import State
from simulator.render import render_part
from simulator.scenarios import (
    JOINING_FORCE_PEAK,
    Scenario,
    all_scenarios,
    scenario,
)
from simulator.stations.base import PartOutcome


@dataclass(frozen=True)
class Part:
    """One part as the plant declared it: when it was inspected, what it rode, what the
    press left in it, and what it genuinely carries."""

    at: datetime
    serial: str
    carrier_id: int
    joining_work: float
    defects: frozenset[str]


@dataclass(frozen=True)
class Change:
    at: datetime
    station: str
    state: State
    reason: str


@dataclass(frozen=True)
class Run:
    origin: datetime
    parts: tuple[Part, ...]
    changes: tuple[Change, ...]
    nodes: dict[str, RecordingNodes]

    def elapsed(self, at: datetime) -> float:
        return (at - self.origin).total_seconds()

    def within(self, lo: float, hi: float) -> tuple[Part, ...]:
        return tuple(p for p in self.parts if lo <= self.elapsed(p.at) < hi)

    def carrying(self, defect: str) -> frozenset[str]:
        """The serials that genuinely carry `defect`. Serials rather than counts,
        because the sharp comparison between two runs is which parts changed."""
        return frozenset(p.serial for p in self.parts if defect in p.defects)

    def stream(self, station: str, signal: str) -> tuple[tuple[float, float], ...]:
        """`(elapsed seconds, value)` for one historised stream."""
        return tuple(
            (self.elapsed(at), float(value))
            for name, at, value in self.nodes[station].writes
            if name == signal
        )

    def settled(self, station: str) -> Change:
        """The last state change this station made -- the condition it ended the run in,
        and when it entered it.

        **The last and not the first, and that is the whole difference between asserting
        a chain and asserting the noise floor.** A running line suspends on its own
        buffers a few percent of the time, so "the first time S2 starved" is answered by
        an ordinary micro-stop somewhere in the warmup. A propagation chain is the one
        that does not clear: each station stops and stays stopped, and the instants they
        stopped at are the chain in order.

        Raises AssertionError if the station never changed state, which means the run
        never started it.
        """
        for change in reversed(self.changes):
            if change.station == station:
                return change
        raise AssertionError(f"{station} never changed state in this run")


async def _run(
    seconds: float,
    *,
    faults: FaultSet = NO_FAULTS,
    settings: Settings | None = None,
    clock: SimulatedClock | None = None,
) -> Run:
    """Run a real four-station line for `seconds` of simulated time.

    The verdict is the plant's own truth rather than the inspection service's, and the
    reason is the line this milestone must not cross: §3.5's scenarios are about what the
    plant did to the parts, and putting the classifier's own two error rates (D7) in
    front of every measurement here would mean asserting the plant's behaviour through
    something else's noise. The classifier is exercised where it belongs -- scenario 6's
    half of it is in the inspection package's own suite.
    """
    settings = settings or Settings()
    clock = clock or new_clock(settings)
    parts: list[Part] = []
    changes: list[Change] = []

    async with httpx.AsyncClient() as http:
        client = InspectionClient(settings, http, faults=faults)

        async def produce(
            part_id: str, carrier_id: int, joining_work: float, at: datetime
        ) -> PartOutcome:
            defects = client.truth_for(part_id, carrier_id, joining_work, at)
            parts.append(
                Part(at, part_id, carrier_id, joining_work, frozenset(defects))
            )
            return PartOutcome(
                disposition="reject" if defects else "good",
                defect_class=defects[0] if defects else None,
                defect_classes=tuple(DEFECT_CLASSES),
                confidences=tuple(
                    0.8 if name in defects else 0.03 for name in DEFECT_CLASSES
                ),
                confidence=0.9,
                image=b"PNG" if defects else None,
                model_version="test-1",
            )

        line, clock, nodes = await build_running_line(
            settings, faults=faults, produce=produce, clock=clock
        )
        horizon = clock.history_start + timedelta(seconds=seconds)
        while (due := line.next_due) is not None and due < horizon:
            before = dict(line.station_states)
            outcome = await line.step()
            assert outcome is not None
            for code, (state, reason) in line.station_states.items():
                if before[code] != (state, reason):
                    changes.append(Change(outcome.at, code, state, reason))

    return Run(clock.history_start, tuple(parts), tuple(changes), nodes)


async def _paired(
    number: int, seconds: float, settings: Settings | None = None
) -> tuple[Run, Run]:
    """The same run with the scenario and without it, on one origin.

    **The comparison this file rests on.** A fault moves a threshold and never a draw
    (`faults`' identity property), so the two runs make the same draws about the same
    parts at the same instants -- and the difference between them is the scenario and
    nothing else. A rate measured against its own before-window carries the sampling
    noise of two small windows; the difference between these two carries none.
    """
    settings = settings or Settings()
    clock = new_clock(settings)
    injected = scenario(number, settings).fault_set(clock.history_start)
    return (
        await _run(seconds, settings=settings, clock=clock),
        await _run(seconds, faults=injected, settings=settings, clock=clock),
    )


async def _scenario_run(number: int, seconds: float, settings: Settings) -> Run:
    """One run carrying scenario `number`, on an origin its offsets are placed on."""
    clock = new_clock(settings)
    return await _run(
        seconds,
        faults=scenario(number, settings).fault_set(clock.history_start),
        settings=settings,
        clock=clock,
    )


def _window(item: Scenario) -> tuple[float, float | None]:
    """The single injection's fault window, as `(at, until)` seconds.

    Every one of §3.5's eight injects exactly one fault today. The assertion is what
    makes that visible rather than assumed: a scenario that grew a second injection
    would otherwise be asserted against its first one's window in silence.
    """
    assert len(item.injections) == 1
    fault = item.injections[0].fault
    return (
        fault.at.total_seconds(),
        None if fault.until is None else fault.until.total_seconds(),
    )


# --- what a scenario may name ---------------------------------------------------------


def test_every_scenario_names_a_carrier_and_a_lane_the_line_actually_has() -> None:
    """A scenario naming carrier 25 of an eighteen-carrier pool, or lane 3 of two, is a
    fault that fires, is written to ground truth and matches no part at all -- the exact
    failure `faults._as_index` refuses a fractional carrier for.

    §4.1 fixes both sets, so this is a range check and not a taste: `LANES` is a closed
    tuple and `carrier_count` is what the pool is built with.
    """
    settings = Settings()
    for item in all_scenarios(settings):
        for fault in item.faults:
            if "carrier" in fault.params:
                assert 0 <= fault.params["carrier"] < settings.carrier_count, (
                    f"scenario {item.number} wears carrier {fault.params['carrier']} of "
                    f"a {settings.carrier_count}-carrier pool"
                )
            if "lane" in fault.params:
                assert int(fault.params["lane"]) in LANES, (
                    f"scenario {item.number} names lane {fault.params['lane']}, and "
                    f"§4.1 gives S1 lanes {LANES}"
                )


def test_the_eight_are_eight_and_each_expects_something_observable() -> None:
    """§3.5's table has eight rows. A scenario with no consequence is an injection
    nothing can be asserted about, which is the shape M2c exists not to ship."""
    items = all_scenarios(Settings())
    assert [item.number for item in items] == list(range(1, 9))
    for item in items:
        assert item.injections, f"scenario {item.number} injects nothing"
        for injection in item.injections:
            assert injection.consequences, (
                f"scenario {item.number} injects {injection.fault.kind} and expects "
                "nothing to follow from it"
            )


# --- 1 and 2: the two ends of the line ------------------------------------------------


@pytest.mark.asyncio
async def test_a_starved_feeder_starves_s2_s3_and_s4_in_that_order() -> None:
    """§3.5 row 1. **The order is the claim**: a line with no buffers at all would
    suspend all three at once, and an assertion that merely counted them would pass on
    one. Each station waits for the buffer above it to drain.

    Measured at the shipped settings, as offsets from the injection: S1 reports
    `starved:feeder` after 3.4 s, S2 `starved:B1_2` after 19.0 s, S3 `starved:B2_3`
    after 41.4 s and S4 `starved:B3_4` after 45.6 s.
    """
    settings = Settings()
    item = scenario(1, settings)
    at, _ = _window(item)
    consequence = item.injections[0].consequences[0]
    assert consequence.within_seconds is not None

    run = await _scenario_run(
        1, at + consequence.within_seconds + settings.takt_seconds, settings
    )

    reached = [
        (station, round(run.elapsed(run.settled(station).at) - at, 1))
        for station in consequence.subjects
    ]
    assert [
        reason for reason in (run.settled(s).reason for s in consequence.subjects)
    ] == [
        "starved:B1_2",
        "starved:B2_3",
        "starved:B3_4",
    ], f"the three did not end starved on the buffer above them: {reached}"
    assert [delay for _, delay in reached] == sorted(delay for _, delay in reached), (
        f"the three did not starve in order: {reached}"
    )
    assert reached[-1][1] <= consequence.within_seconds, (
        f"the chain took {reached[-1][1]} s and the ground-truth log claims "
        f"{consequence.within_seconds} s: {reached}"
    )
    # And S1 itself names the thing outside the line, which no buffer rule can produce.
    assert run.settled("S1_Feeding").reason == "starved:feeder"


@pytest.mark.asyncio
async def test_a_blocked_outfeed_backs_the_blockage_up_to_s1() -> None:
    """§3.5 row 2, and scenario 1's mirror: the same three buffers carry the condition
    the other way, so the pair proves the buffers rather than the stations.

    Measured as offsets from the injection: S4 `blocked:outfeed` after 4.2 s, S3
    `blocked:B3_4` after 29.5 s, S2 `blocked:B2_3` after 36.5 s, S1 `blocked:B1_2`
    after 49.3 s.
    """
    settings = Settings()
    item = scenario(2, settings)
    at, _ = _window(item)
    consequence = item.injections[0].consequences[0]
    assert consequence.within_seconds is not None

    run = await _scenario_run(
        2, at + consequence.within_seconds + settings.takt_seconds, settings
    )

    reached = [
        (station, round(run.elapsed(run.settled(station).at) - at, 1))
        for station in consequence.subjects
    ]
    assert [run.settled(s).reason for s in consequence.subjects] == [
        "blocked:B3_4",
        "blocked:B2_3",
        "blocked:B1_2",
    ], f"the three did not end blocked on the buffer below them: {reached}"
    assert [delay for _, delay in reached] == sorted(delay for _, delay in reached), (
        f"the blockage did not reach the three in order: {reached}"
    )
    assert reached[-1][1] <= consequence.within_seconds
    assert run.settled("S4_Outfeed").reason == "blocked:outfeed"


@pytest.mark.asyncio
async def test_a_clean_line_never_names_a_condition_outside_itself() -> None:
    """The other half of the two gates above, and the one that would fail silently.

    A feeder threshold one unit too high, or an outfeed one part too low, would put
    `starved:feeder` and `blocked:outfeed` into the history of a line with nothing wrong
    with it -- and every later milestone would be looking for scenarios 1 and 2 in a
    plant that reports them on a clean run. Measured over four simulated hours: the only
    reasons the line gives are the ordinary buffer ones.
    """
    run = await _run(4.0 * 3600)
    assert run.parts, "the line produced nothing, so this proves nothing"
    reasons = {change.reason for change in run.changes if change.reason}
    assert "starved:feeder" not in reasons
    assert "blocked:outfeed" not in reasons
    assert reasons <= {
        "starved:B1_2",
        "starved:B2_3",
        "starved:B3_4",
        "blocked:B1_2",
        "blocked:B2_3",
        "blocked:B3_4",
        "starved:carrier-return",
    }, f"a clean line reported {sorted(reasons)}"


# --- 3 and 7: one symptom, two causes -------------------------------------------------


@pytest.mark.asyncio
async def test_a_drifting_clamp_lowers_the_peak_and_gaps_parts_that_were_not() -> None:
    """§3.5 row 3. Both halves, because the row is only a trap for row 7 while both are
    true: the published force falls **and** parts start carrying `gap`.

    Measured at the shipped drift: `JoiningForcePeak` 4214.3 N before the injection
    against 3792.5 N once the hour-long ramp has run, a fall of 421.8 N against a
    part-to-part spread of 39.5 N -- 10.7 sigma. Every part the clean run gapped is
    still gapped, and the drifted run adds more, which is the difference between a fault
    that moved a threshold and one that reached the draw.
    """
    settings = Settings()
    item = scenario(3, settings)
    at, _ = _window(item)
    ramp = settings.joining_force_drift_ramp_seconds
    clean, drifted = await _paired(3, at + ramp + 3600.0, settings)

    station, signal = JOINING_FORCE_PEAK.split(".")
    before = [
        value for elapsed, value in drifted.stream(station, signal) if elapsed < at
    ]
    after = [
        value
        for elapsed, value in drifted.stream(station, signal)
        if elapsed > at + ramp
    ]
    fall = statistics.fmean(before) - statistics.fmean(after)
    assert fall > 0.5 * abs(settings.joining_force_drift_newtons), (
        f"the peak fell {fall:.1f} N on a {settings.joining_force_drift_newtons} N "
        "drift: the clamp is not reaching the trace"
    )

    assert clean.carrying(GAP) < drifted.carrying(GAP), (
        "the drift did not gap a single part the clean run did not, or un-gapped one: "
        "the press has no path to the defect draw"
    )
    # The classes the press does not decide are untouched -- a rise in all six would be
    # a line-wide drift, which is a different fault with a different diagnosis.
    for name in DEFECT_CLASSES:
        if name != GAP:
            assert clean.carrying(name) == drifted.carrying(name), (
                f"the clamp drift changed {name}, which no press decides"
            )


@pytest.mark.asyncio
async def test_a_bad_lot_gaps_parts_while_the_force_stream_stays_put() -> None:
    """§3.5 row 7, the strongest test in the set and the one that is easiest to ruin.

    Its symptom is row 3's symptom. **The force is the whole of what separates them**,
    so a scenario that also drifted the force would have quietly become scenario 3 and
    this is the assertion that says it did not.

    Measured across the bad lot's window: the mean `JoiningForcePeak` moves from
    4214.09 N to 4211.77 N, a shift of 2.32 N against the stream's own part-to-part
    spread of 39.5 N -- **0.06 sigma, against scenario 3's 10.7**. It is not exactly
    zero and the reason is worth knowing: the peak is the largest sample of a noisy
    trace, and a part met later is clamped for fewer of them, so the maximum of the
    noise over the clamped stretch is drawn from a slightly smaller sample. The shift is
    an order statistic, it is two hundred times below the drift it has to be
    distinguished from, and it is far below the noise it sits in.

    Meanwhile the joining work falls 14688.9 -> 12198.7 N.mm (17.0 %) and the `gap` rate
    inside the window goes from nothing to 2.8 %.
    """
    settings = Settings()
    item = scenario(7, settings)
    at, until = _window(item)
    assert until is not None
    clean, bad = await _paired(7, until + 600.0, settings)

    station, signal = JOINING_FORCE_PEAK.split(".")
    outside = [v for elapsed, v in bad.stream(station, signal) if elapsed < at]
    inside = [v for elapsed, v in bad.stream(station, signal) if at <= elapsed < until]
    spread = statistics.stdev(outside)
    shift = abs(statistics.fmean(inside) - statistics.fmean(outside))
    assert shift < 0.25 * spread, (
        f"the force moved {shift:.2f} N across the bad lot against a {spread:.2f} N "
        "part-to-part spread: this scenario has become scenario 3, and the one thing "
        "that distinguishes it is gone"
    )

    # The work is where an undersized component *is* visible, and it is curve-only:
    # neither published scalar carries it (§3.4a).
    work_outside = statistics.fmean(p.joining_work for p in bad.within(0.0, at))
    work_inside = statistics.fmean(p.joining_work for p in bad.within(at, until))
    assert work_inside < 0.9 * work_outside, (
        f"joining work {work_outside:.0f} -> {work_inside:.0f} N.mm: the contact point "
        "is not moving, so nothing distinguishes the lot at all"
    )

    gapped = bad.carrying(GAP) - clean.carrying(GAP)
    assert gapped, "the bad lot gapped no part the clean run did not"
    by_window = [p for p in bad.within(at, until) if p.serial in gapped]
    assert len(by_window) >= 0.9 * len(gapped), (
        f"only {len(by_window)} of {len(gapped)} newly gapped parts were inspected "
        "inside the lot's window: the defects do not correlate with the lot"
    )


@pytest.mark.asyncio
async def test_the_bad_lots_window_is_the_lot_the_line_really_drew_from() -> None:
    """Scenario 7 names its lot by lane and index and turns that into a window at the
    line's nominal takt, because a lot that has not loaded yet has no code to name
    (`identity._LOT_NUMBER_CEILING` has the account).

    The run's own jitter and micro-stops move the real boundary, so the window is an
    approximation and this is what measures it: **98.2 % of the parts fed inside the
    window came off lot 1**, the window covering S1's parts 509-1009 against the lot's
    501-1000. The other 1.8 % is the accumulated takt drift over five hundred parts.
    """
    settings = Settings()
    at, until = _window(scenario(7, settings))
    assert until is not None
    run = await _run(until + settings.takt_seconds)

    fed = [
        int(value)
        for elapsed, value in run.stream("S1_Feeding", "PartCount")
        if at <= elapsed < until
    ]
    assert fed, "no part was fed inside the window"
    lot = range(
        settings.bad_lot_index * settings.lot_size + 1,
        (settings.bad_lot_index + 1) * settings.lot_size + 1,
    )
    overlap = len([index for index in fed if index in lot]) / len(fed)
    assert overlap > 0.95, (
        f"only {overlap:.1%} of the parts in scenario 7's window came off lot "
        f"{settings.bad_lot_index}: the window and the lot have drifted apart, and the "
        "defects would correlate with a period rather than with a lot"
    )


@pytest.mark.asyncio
async def test_one_defective_component_is_one_bad_part() -> None:
    """§3.5 row 8, scenario 7's mirror, and it exists to stop over-generalising: one
    bad component must read as one bad part and not as a lot problem.

    Measured at the shipped offset: exactly one part, A-00000355, gains a `gap` that the
    clean run did not give it -- pressed inside the one-takt window and inspected 35 s
    later, five buffers downstream.
    """
    settings = Settings()
    item = scenario(8, settings)
    at, until = _window(item)
    assert until is not None
    assert until - at < min(settings.station_takt_seconds.values()), (
        "the window is wider than a station takt, so more than one part could be "
        "pressed inside it"
    )

    clean, one = await _paired(8, at + 900.0, settings)
    gained = one.carrying(GAP) - clean.carrying(GAP)
    assert len(gained) == 1, (
        f"{len(gained)} parts gained a gap from one defective component: {sorted(gained)}"
    )


# --- 5: one lane, two classes ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_contaminated_lane_raises_its_two_classes_and_leaves_the_other_four() -> (
    None
):
    """§3.5 row 5. What is assertable is that the two classes §3.5 names rise and the
    other four do not move at all -- which is what separates this from a line-wide rise.

    Measured over 19,800 parts at the shipped factor: `missing_part | contamination`
    goes from 0.520 % to 1.864 %, a factor of 3.6, while the other four classes are
    **identical part for part**, because a fault moves a threshold and never a draw.

    **The lane itself is not recoverable from a part record, and M3 has to know it.**
    Every assembly draws one component from each lane, so every part contains lane 2,
    and both lanes roll their lots on the same part -- so lane 1's lots and lane 2's
    lots cover exactly the same parts. The lane is in the plant's truth (the draw is per
    (lane, class)); the evidence for it is not in the verdict.
    """
    settings = Settings()
    item = scenario(5, settings)
    raised = set(item.injections[0].consequences[0].subjects)
    origin = new_clock(settings).history_start
    faults = item.fault_set(origin)
    at = origin + timedelta(seconds=settings.scenario_warmup_seconds + 3600.0)

    async with httpx.AsyncClient() as http:
        clean = InspectionClient(settings, http)
        dirty = InspectionClient(settings, http, faults=faults)
        nominal = settings.press_nominal_work
        hits = {"clean": 0, "dirty": 0}
        others = {"clean": 0, "dirty": 0}
        parts = 19_800
        for index in range(parts):
            carrier = index % settings.carrier_count
            serial = f"A-{index:08d}"
            for name, client in (("clean", clean), ("dirty", dirty)):
                found = set(client.truth_for(serial, carrier, nominal, at))
                if found & raised:
                    hits[name] += 1
                if found - raised:
                    others[name] += 1

    assert hits["dirty"] > 2 * hits["clean"], (
        f"{sorted(raised)} rose only from {hits['clean']} to {hits['dirty']} in "
        f"{parts} parts: the contamination is inside the pool's own spread"
    )
    assert others["dirty"] == others["clean"], (
        "the contamination moved a class §3.5 does not name for it, which would make "
        "this a line-wide rise rather than one lane's"
    )


# --- 6: a fouled lens, on the simulator's side ----------------------------------------


def test_a_fouled_lens_renders_a_frame_with_less_contrast_in_it() -> None:
    """D8, the half that lives here: the image is genuinely degraded, so the decay the
    classifier reports is read off pixels rather than declared.

    Measured over the fouling scenario's own factor: a clean frame carries 66.36 grey
    levels of RMS contrast and a frame rendered at clarity 0.55 carries 36.75 -- 0.554
    of it, so the statistic tracks the fouling almost exactly. The other half, that the
    confidences fall with it and the verdict does not, is in the inspection package's
    own suite, because the classifier is what owns it.
    """
    settings = Settings()
    item = scenario(6, settings)
    clarity = item.injections[0].fault.params["factor"]

    def contrast(value: float) -> float:
        frames = [
            render_part(
                f"A-{index:08d}",
                [],
                settings.image_width,
                settings.image_height,
                settings.seed,
                settings.image_compress_level,
                value,
            )
            for index in range(20)
        ]
        return statistics.fmean(_rms_contrast(frame) for frame in frames)

    clean = contrast(1.0)
    fouled = contrast(clarity)
    assert fouled < clean
    assert abs(fouled / clean - clarity) < 0.05, (
        f"a frame rendered at clarity {clarity} carries {fouled / clean:.3f} of a clean "
        "frame's contrast: the renderer's veil and the statistic have drifted apart, "
        "and scenario 6's decay would no longer be proportional to the fouling"
    )


def _rms_contrast(frame: bytes) -> float:
    """The simulator's own reading of `inspection.classifier.contrast_of`.

    Duplicated for the reason `simulator.render` duplicates `inspection.render`: the two
    packages are separate uv workspace members and may not import each other (§10.7).
    Three lines, and the inspection suite is what pins the copy that matters.
    """
    import io

    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(frame)) as image:
        return float(ImageStat.Stat(image.convert("L")).stddev[0])


# --- 4 is measured in test_noise ------------------------------------------------------
#
# `test_one_worn_carrier_is_distinguishable_from_the_baseline_spread` measures §3.5 row
# 4's concentration at the production depth, against the shipped `scenario(4, ...)`
# itself: carrier 7 reaches d = +5.84 on the `misalignment | scratch` query while a clean
# run's worst carrier reaches +2.52, and the line's own rate moves from 0.571 % to
# 0.636 % -- the "no stop" half of the row. Measuring it twice would be the same 19,800
# parts drawn again for the same answer.
