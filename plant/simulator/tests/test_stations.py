"""The four stations' own behaviour. Node writes are captured through a recording
double for the address space, so these assert what each station *records*, never how
it reaches asyncua."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from conftest import RecordingNodes, build_running_line
from pydantic import ValidationError
from simulator.address_space import (
    PACKML_SIGNALS,
    STATION_LIVE_SIGNALS,
    STATION_SIGNALS,
)
from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.curve import peak_of, work_of
from simulator.events import (
    ASSEMBLY_CREATED,
    COMPONENT_READ,
    INSPECTION_RESULT,
    PART_COMPLETED,
    PART_PROCESSED,
)
from simulator.identity import LANES, LotSchedule, load_carrier
from simulator.inspection_client import DEFECT_CLASSES
from simulator.line import PartState
from simulator.packml import State
from simulator.stations import (
    FeedingStation,
    InspectionStation,
    JoiningStation,
    OutfeedStation,
    Station,
)
from simulator.stations.base import PartOutcome

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
TAKT = timedelta(seconds=Settings().takt_seconds)
"""One cycle apart on the simulated timeline, for the tests that run a station more
than once: `LotSchedule.draw` refuses two draws off one lane at one instant."""

# §4.1's browse names, which is what the real address space gives
# `StationNodes.code` and what `Settings.station_takt_seconds` is keyed by.
_CODES: dict[type[Station], str] = {
    FeedingStation: "S1_Feeding",
    JoiningStation: "S2_Joining",
    InspectionStation: "S3_Inspection",
    OutfeedStation: "S4_Outfeed",
}


def loaded(carrier_id: int = 0, at: datetime = T0) -> PartState:
    """A part as the station under test receives it.

    The assembly S1 created, and the joining work a nominal press would have left --
    every station below S1 refuses a part with no assembly and S3 refuses one with no
    press, so this is what a real cycle actually hands them. Building it through
    `identity.load_carrier` rather than by hand is what keeps it the same object S1
    produces, and S2 overwrites the work with what its own press did.

    The nominal rather than nothing, because these tests drive one station at a time and
    a part that reached S3 without passing S2 is an artefact of that, not a case. The
    real guard has its own test below.
    """
    return PartState(
        assembly=load_carrier(LotSchedule(Settings(), at), 0, carrier_id, at),
        joining_work=Settings().press_nominal_work,
    )


def build_one(
    factory: type[Station], always_reject: bool | None = None
) -> tuple[Station, RecordingNodes]:
    """Each station is constructed exactly as `server.build_line` constructs it, minus
    the real address space."""
    settings = Settings()
    nodes = RecordingNodes(_CODES[factory])
    if factory is FeedingStation:
        return (
            FeedingStation(nodes, settings, seed=1, schedule=LotSchedule(settings, T0)),
            nodes,
        )
    if factory is InspectionStation:

        async def produce(
            _serial: str, _carrier_id: int, _joining_work: float, _at: datetime
        ) -> PartOutcome:
            reject = bool(always_reject)
            return PartOutcome(
                disposition="reject" if reject else "good",
                defect_class="gap" if reject else None,
                defect_classes=tuple(DEFECT_CLASSES),
                # §3.4's six independent scores. `gap` alone is high on a reject and
                # all six are low on a good part, and the six never sum to 1 -- which
                # is the shape a softmax could not produce and scenario 6 needs.
                confidences=tuple(
                    0.81 if reject and name == "gap" else 0.06
                    for name in DEFECT_CLASSES
                ),
                # Confidence in the OK/NOK verdict, not in a class (§3.4): high either
                # way, because the classifier is sure of the verdict either way.
                confidence=0.9,
                image=b"PNG" if reject else None,
                model_version="test-1",
            )

        return InspectionStation(nodes, settings, seed=1, produce=produce), nodes
    return factory(nodes, settings, seed=1), nodes


def build_all() -> list[tuple[Station, RecordingNodes]]:
    return [
        build_one(factory)
        for factory in (
            FeedingStation,
            JoiningStation,
            InspectionStation,
            OutfeedStation,
        )
    ]


@pytest.mark.asyncio
async def test_every_station_writes_only_signals_the_tree_gives_it() -> None:
    """§4.1 fixes each station's variables, and `StationNodeSet.write` raises KeyError
    for one it was not given -- so a station writing a signal the tree does not carry
    is a crash on the first cycle of a real boot.

    This is the check that was missing. Its predecessor asserted that all four write
    `TaktTime` and `PartCount` against a double that accepts any name, and §4.1 gives
    S4 no `PartCount` at all (its count is `GoodCount` + `RejectCount`): the assertion
    passed while the shipped wiring could not run one cycle.

    Both dicts are bounded, not just the historised one. `StationNodeSet.write_live`
    raises the same KeyError for a live-only signal §4.1 does not give a station, and
    D12 gives three of them to S1 and none to anyone else -- so a station reaching for
    `CurrentAssemblySerial` that has no such node is the identical crash on the first
    cycle of a real boot.
    """
    for station, nodes in build_all():
        # Every station below S1 refuses a part with no assembly, and S4 also refuses
        # one with no disposition, so each is handed the part it would really receive.
        part = loaded()
        if station.code == _CODES[OutfeedStation]:
            part.disposition = "good"
        await station.run_cycle(T0, Carrier(0), part)
        declared = {name for name, _ in PACKML_SIGNALS + STATION_SIGNALS[station.code]}
        assert nodes.signals() <= declared, station.code
        declared_live = {name for name, _ in STATION_LIVE_SIGNALS.get(station.code, ())}
        assert nodes.live_signals() <= declared_live, station.code
        # A station's two node dicts are disjoint in the tree, so its two write paths
        # must be too: a signal reached through the wrong one is either a historised
        # stream the ledger never counted or a live node with a ledger row the
        # historian can never hold.
        assert nodes.signals() & nodes.live_signals() == set(), station.code
        # TaktTime is the one signal every station has and every station must write:
        # it is what makes the takt a measured number rather than a configured one.
        assert "TaktTime" in nodes.signals(), station.code


@pytest.mark.asyncio
async def test_every_station_publishes_the_parts_it_has_handled() -> None:
    """Three stations publish a running `PartCount`; S4 publishes the same count as
    `GoodCount` and `RejectCount`, which sum to it. Either way a part that passes
    through a station is visible in that station's own numbers -- a station whose
    counters never moved is one nothing can tell apart from a stopped one."""
    for station, nodes in build_all():
        outfeed = station.code == _CODES[OutfeedStation]
        part = loaded()
        if outfeed:
            part.disposition = "good"
        await station.run_cycle(T0, Carrier(0), part)
        counters = (
            {"GoodCount", "RejectCount"} if outfeed else {station.part_count_signal}
        )
        assert counters <= nodes.signals(), station.code


@pytest.mark.asyncio
async def test_part_count_is_monotonic_across_cycles() -> None:
    station, nodes = build_one(FeedingStation)
    for cycle in range(3):
        # A distinct instant per cycle: `LotSchedule.draw` refuses two draws off one
        # lane at one instant, because a component's lot would then be ambiguous at
        # exactly the instant a containment query needs it.
        await station.run_cycle(T0 + cycle * TAKT, Carrier(cycle), PartState())
    counts = [value for signal, _, value in nodes.writes if signal == "PartCount"]
    assert counts == [1, 2, 3]


@pytest.mark.asyncio
async def test_feeding_draws_both_lanes_down_together() -> None:
    """Both lanes supply every assembly, one component each, so both fall by
    `lane_draw_per_part` per part and neither is ahead of the other.

    This replaces an M2a test that asserted lane 2 stood exactly `lane_draw_per_part`
    above lane 1 -- the alternating-feeder model S1 used before it had components to
    draw. `identity.load_carrier` settles it: one component per lane per assembly, so
    the old assertion pinned a fiction. What separates the two lanes is which lot each
    is drawing from (M2c's scenarios 5 and 7), not the shape of its level.

    Two stations, because the two claims need opposite noise settings. The sawtooth is
    only checkable with the sensor off, and "two measurements rather than one number
    published twice" is only checkable with it on -- with `lane_fill_sigma=0` an
    implementation that drew once and published the result to both lanes satisfies
    every assertion in the first half.
    """
    # Measurement noise off, because the claim is about the draw rather than about the
    # sensor: at the shipped `lane_fill_sigma` the per-part 0.5 is inside one sigma of
    # the noise on each lane, and this would be reading the Gaussian.
    settings = Settings(lane_fill_sigma=0.0)
    nodes = RecordingNodes(_CODES[FeedingStation])
    station = FeedingStation(
        nodes, settings, seed=1, schedule=LotSchedule(settings, T0)
    )

    await station.run_cycle(T0, Carrier(0), PartState())

    # A fill level is a number; the isinstance filter is what makes that a claim rather
    # than an assumption, because RecordingNodes takes `float | str` and the equality
    # below fails if either lane arrived as text.
    levels = {
        signal: value
        for signal, _, value in nodes.writes
        if signal.startswith("LaneFill_") and isinstance(value, float)
    }
    assert set(levels) == {"LaneFill_1", "LaneFill_2"}
    assert levels["LaneFill_1"] == pytest.approx(levels["LaneFill_2"])
    assert levels["LaneFill_1"] == pytest.approx(
        settings.lane_capacity - settings.lane_draw_per_part
    )

    # ...and the level is genuinely a level, not a constant: a second part takes
    # another `lane_draw_per_part` off both. The isinstance filter is here for the same
    # reason it is above -- `RecordingNodes` takes `float | str`.
    await station.run_cycle(T0 + TAKT, Carrier(1), PartState())
    after = [
        value
        for signal, _, value in nodes.writes
        if signal == "LaneFill_1" and isinstance(value, float)
    ]
    assert len(after) == 2
    assert after[1] == pytest.approx(after[0] - settings.lane_draw_per_part)

    # At the shipped noise the two lanes are two measurements of two levels, not one
    # draw published under two names -- which is the half a sigma of zero cannot see.
    # `!=` on the rounded floats, because that is exactly what a shared draw could not
    # produce: `_lane_level()` is called once per lane and each call takes its own
    # Gaussian, so the two land on different values essentially always at
    # `lane_fill_sigma=0.4` and three decimal places.
    noisy_settings = Settings()
    assert noisy_settings.lane_fill_sigma > 0.0, (
        "this half of the test is vacuous at a sigma of zero, which is the defect it "
        "exists to close"
    )
    noisy_nodes = RecordingNodes(_CODES[FeedingStation])
    noisy = FeedingStation(
        noisy_nodes, noisy_settings, seed=1, schedule=LotSchedule(noisy_settings, T0)
    )
    await noisy.run_cycle(T0, Carrier(0), PartState())
    noisy_levels = {
        signal: value
        for signal, _, value in noisy_nodes.writes
        if signal.startswith("LaneFill_") and isinstance(value, float)
    }
    assert noisy_levels["LaneFill_1"] != noisy_levels["LaneFill_2"]


@pytest.mark.asyncio
async def test_joining_records_a_peak_force_and_a_distance() -> None:
    """§4.1's two S2 process signals. The force-distance curve behind them is M2b."""
    station, nodes = build_one(JoiningStation)
    await station.run_cycle(T0, Carrier(0), loaded())
    assert {"JoiningForcePeak", "JoiningDistance"} <= nodes.signals()


@pytest.mark.asyncio
async def test_inspection_puts_its_verdict_on_the_part() -> None:
    """S4 sorts on this. Without it GoodCount and RejectCount would have to be
    reconstructed by time-joining, which is the inference §3.4a forbids."""
    station, nodes = build_one(InspectionStation)
    part = loaded()
    await station.run_cycle(T0, Carrier(0), part)
    assert part.disposition in ("good", "reject")
    assert len(nodes.events) == 1


@pytest.mark.asyncio
async def test_only_rejects_carry_an_image() -> None:
    """§3.4, and it is what keeps images inside the single permitted channel."""
    station, nodes = build_one(InspectionStation, always_reject=True)
    await station.run_cycle(T0, Carrier(0), loaded())
    assert nodes.payloads(INSPECTION_RESULT)[0]["Image"]

    good, good_nodes = build_one(InspectionStation, always_reject=False)
    await good.run_cycle(T0, Carrier(0), loaded())
    assert not good_nodes.payloads(INSPECTION_RESULT)[0]["Image"]


@pytest.mark.asyncio
async def test_the_inspection_event_carries_a_vector_not_a_scalar() -> None:
    """§3.4: six independent scores in [0, 1] that do NOT sum to 1.

    Scenario 6 needs every class's score able to fall together, which is impossible
    under a softmax over six values, and scenarios 4 and 5 each need two classes high
    on one part -- pattern DP-02 is keyed on a pair. M1's single `DefectClass` and
    single `Confidence` can express neither.

    The names ride beside the scores and are asserted as a set against this workspace's
    own copy of the vocabulary: a vector on the wire with its key agreed privately
    somewhere else is a vector that gets decoded against the wrong names.
    """
    station, nodes = build_one(InspectionStation, always_reject=True)
    await station.run_cycle(T0, Carrier(0), loaded())
    fields = nodes.payloads(INSPECTION_RESULT)[0]

    classes = fields["DefectClasses"]
    scores = fields["Confidences"]
    assert isinstance(classes, list) and isinstance(scores, list)
    assert set(classes) == set(DEFECT_CLASSES)
    assert len(scores) == len(classes) == 6
    assert all(0.0 <= score <= 1.0 for score in scores)
    assert sum(scores) != pytest.approx(1.0), (
        "six independent scores, not a distribution -- a vector that sums to 1 is one "
        "scenario 6 cannot make fall across all six classes at once"
    )


@pytest.mark.asyncio
async def test_a_good_part_scores_low_on_all_six_and_is_confident() -> None:
    """The exact defect §3.4 records. Reading the vector as a distribution reported a
    good part as 27 % confident and ~30 % misaligned; §3.4 is explicit that the scalar
    `Confidence` is confidence in the OK/NOK *verdict*, not in any class. A good part
    scores low on all six and is confidently good."""
    station, nodes = build_one(InspectionStation, always_reject=False)
    await station.run_cycle(T0, Carrier(0), loaded())
    fields = nodes.payloads(INSPECTION_RESULT)[0]

    scores = fields["Confidences"]
    assert isinstance(scores, list)
    assert fields["Disposition"] == "good"
    assert all(score < 0.5 for score in scores)
    # The scalar and the vector are different claims, and this is the pair that says
    # so: every class low *and* the verdict confident, at the same time. The isinstance
    # is not decoration: `fields` is `dict[str, object]` because that is what goes on
    # the wire, and a comparison against `object` would be a type error rather than the
    # claim this test makes.
    verdict = fields["Confidence"]
    assert isinstance(verdict, float)
    assert verdict > 0.5


@pytest.mark.asyncio
async def test_outfeed_counts_good_and_reject_separately() -> None:
    station, nodes = build_one(OutfeedStation)
    for carrier_id, disposition in enumerate(("good", "reject", "reject")):
        part = loaded(carrier_id)
        part.disposition = disposition
        await station.run_cycle(T0, Carrier(carrier_id), part)

    good = [v for s, _, v in nodes.writes if s == "GoodCount"]
    reject = [v for s, _, v in nodes.writes if s == "RejectCount"]
    assert good[-1] == 1
    assert reject[-1] == 2


@pytest.mark.asyncio
async def test_outfeed_refuses_a_part_nobody_inspected() -> None:
    """A part reaching S4 with no disposition means S3 did not run, and sorting it
    as good would be a quiet wrong answer -- exactly what this system exists not to
    give."""
    station, _ = build_one(OutfeedStation)
    with pytest.raises(ValueError, match="disposition"):
        await station.run_cycle(T0, Carrier(0), loaded())


@pytest.mark.asyncio
async def test_a_state_change_is_written_with_its_reason() -> None:
    station, nodes = build_one(FeedingStation)
    await station.publish_state(T0, State.SUSPENDED, "starved:carrier-return")
    assert ("State", T0, "Suspended") in nodes.writes
    assert ("StateReason", T0, "starved:carrier-return") in nodes.writes


def test_each_station_takes_its_own_nominal_takt() -> None:
    """§3.1: S3 paces the line and every other station runs faster, so B1_2 and B2_3
    fill and B3_4 drains. One shared takt would leave every buffer oscillating between
    empty and one, and buffer capacity would bound nothing.

    **S4 is strictly faster than S3, not equal to it.** Equal takts give B3_4 no
    restoring force: it becomes a driftless random walk, and §3.5's micro-stops are what
    walk it -- measured at 19.5 % of the time full, with the bottleneck blocked behind
    it. S4 sits between S2 and S3, close enough to S3 that it is not starved on every
    cycle and far enough that B3_4 comes back to empty.

    Twenty thousand draws rather than five hundred: a micro-stop adds up to 25 s to one
    takt, so a mean over a few hundred draws is decided by whether a jam happened to
    land in the window rather than by the nominal takt this is about.
    """
    means = {}
    for factory in (FeedingStation, JoiningStation, InspectionStation, OutfeedStation):
        station, _ = build_one(factory)
        draws = [station.next_takt() for _ in range(20_000)]
        means[station.code] = sum(draws) / len(draws)

    assert (
        means["S1_Feeding"]
        < means["S2_Joining"]
        < means["S4_Outfeed"]
        < means["S3_Inspection"]
    )


def test_the_takt_stream_varies_and_a_sigma_that_flattens_it_is_refused() -> None:
    """What D13 deleted along with the resample guard, restored where it belongs.

    The guard raised on `takt_jitter_sigma=0` -- "the obvious way someone turns jitter
    off", in its own words -- and its removal turned that loud failure into a silent
    one: asyncua historises a stream only where it changes, so a zero sigma leaves
    TaktTime at the station's nominal except on the ~0.1 % of cycles a micro-stop lands
    on. Measured at 30 distinct values in 20,000 cycles and 59 historised rows, with
    reconciliation still green, because `Ledger.record` drops exactly the same rows.
    That is the quiet wrong answer, and it is not what D13 asked for.

    Both halves: the stream is genuinely varied at the shipped sigma, and a sigma that
    would flatten it is refused while the line is being built.
    """
    station, _ = build_one(InspectionStation)
    draws = [station.next_takt() for _ in range(20_000)]
    assert len(set(draws)) == len(draws)

    with pytest.raises(ValidationError, match="TaktTime"):
        Settings(takt_jitter_sigma=0.0)


def test_a_station_the_settings_do_not_name_is_refused() -> None:
    """The station set is closed -- §4.1 fixes four and address_space.STATION_SIGNALS
    enumerates them -- so a code the configuration does not name is an operator error,
    not a station to run at the line-wide default.

    This is the mechanism, not the instance. The shipped defaults were keyed "S1".."S4"
    while StationNodes.code carries "S1_Feeding"..; fixing the defaults does not stop
    PLANT_STATION_TAKT_SECONDS reintroducing exactly that at runtime, and a fallback
    would absorb it into a perfectly balanced line where every buffer oscillates
    between empty and one and §3.1's propagation claim quietly stops being true.
    """
    settings = Settings(station_takt_seconds={"S1": 5.7})
    with pytest.raises(ValueError, match="no takt configured for station"):
        FeedingStation(
            RecordingNodes("S1_Feeding"),
            settings,
            seed=1,
            schedule=LotSchedule(settings, T0),
        )


_DRAW_PROBE = """
from datetime import UTC, datetime

from simulator.config import Settings
from simulator.identity import LotSchedule
from simulator.stations import FeedingStation


class Nodes:
    code = "S1_Feeding"

    async def write(self, signal, at, value):
        raise AssertionError("the probe never cycles")

    async def write_live(self, signal, at, value):
        raise AssertionError("the probe never cycles")

    async def trigger_event(self, event, at, fields):
        raise AssertionError("the probe never cycles")


settings = Settings()
started = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
station = FeedingStation(
    Nodes(), settings, seed=settings.seed, schedule=LotSchedule(settings, started)
)
print(repr([station.next_takt() for _ in range(5)]))
"""


def _draws_under(hash_seed: str) -> str:
    """S1's first five takts, drawn in a fresh interpreter at this PYTHONHASHSEED."""
    completed = subprocess.run(
        [sys.executable, "-c", _DRAW_PROBE],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    return completed.stdout.strip()


def test_the_same_seed_draws_the_same_takts_in_every_process() -> None:
    """§3.6, and the reason it needs its own process to be tested at all.

    Station RNGs are seeded from the station code, and the obvious way to fold a
    string into a seed -- hash() -- is salted per interpreter by PYTHONHASHSEED. A
    plant seeded that way reproduces itself perfectly within one run and differs on
    every boot, which is the guarantee §3.6 makes, broken where nothing in a single
    process can see it: test_line_queue's determinism test runs both of its traces in
    one interpreter and stays green under exactly that defect.

    So this spawns real interpreters. `random` is included because a fixed set of
    seeds could in principle be satisfied by a hash that happened to agree on them.
    """
    outputs = {_draws_under(seed) for seed in ("1", "2", "3", "random")}
    assert len(outputs) == 1, f"the seed did not survive a new process: {outputs}"

    takts = ast.literal_eval(outputs.pop())
    # Guards against the probe going quietly vacuous -- an empty list would satisfy
    # the equality above no matter how the RNG were seeded.
    assert len(takts) == 5
    assert len(set(takts)) == 5


# --- identity, from the station that creates it to the station that retires it -------


@pytest.mark.asyncio
async def test_the_assembly_records_the_two_components_it_was_built_from() -> None:
    """§3.1's asymmetric identity, as S1 publishes it: one component off each lane,
    each carrying its own lot and supplier, and the assembly naming both in `LANES`
    order -- which is what makes a component's position in that array §5.2's
    `genealogy.position`."""
    station, nodes = build_one(FeedingStation)
    await station.run_cycle(T0, Carrier(4), PartState())

    reads = nodes.payloads(COMPONENT_READ)
    created = nodes.payloads(ASSEMBLY_CREATED)
    assert len(reads) == len(LANES)
    assert len(created) == 1

    assert [read["Lane"] for read in reads] == list(LANES)
    # One lot code per lane and never the same on both, which is what keeps M2c's
    # scenario 5 (one lane) and scenario 7 (one lot) two different scenarios.
    assert len({read["LotCode"] for read in reads}) == len(LANES)
    assert all(read["Supplier"] for read in reads)

    assert created[0]["ComponentSerials"] == [read["ComponentSerial"] for read in reads]
    assert created[0]["CarrierId"] == 4


@pytest.mark.asyncio
async def test_s1_publishes_the_live_only_lot_and_serial_nodes() -> None:
    """D12: in the tree, written every cycle, never historised. The events above are
    the authoritative copy; these are what an HMI reads without asking for history.

    Asserted through `live_writes` rather than `writes`, which is the half that
    matters: the same three values written through the historised path would reconcile
    against a ledger that has no rows for them and fail every boot.
    """
    station, nodes = build_one(FeedingStation)
    await station.run_cycle(T0, Carrier(0), PartState())

    assert nodes.live_signals() == {"Lane1_Lot", "Lane2_Lot", "CurrentAssemblySerial"}
    assert nodes.live_signals() & nodes.signals() == set()
    serial = nodes.payloads(ASSEMBLY_CREATED)[0]["AssemblySerial"]
    assert ("CurrentAssemblySerial", T0, serial) in nodes.live_writes


@pytest.mark.asyncio
async def test_s2_presses_against_the_serial_not_against_the_clock() -> None:
    """§3.4a: the association is known exactly at the instant of production and only
    approximately afterwards.

    So the press goes out carrying the serial it was performed on -- not a timestamp
    for something downstream to join "which part was at S2 at 02:14:07" against, which
    is the inference the spec forbids and what makes a containment list unusable at the
    moment it matters. The two summary scalars on the event are the same numbers, to
    the digit, as the two historised streams, so the per-part record and the time
    series can never read as two measurements of one press.
    """
    station, nodes = build_one(JoiningStation)
    part = loaded()
    await station.run_cycle(T0, Carrier(0), part)

    assert part.assembly is not None
    fields = nodes.payloads(PART_PROCESSED)[0]
    assert fields["AssemblySerial"] == part.assembly.serial

    streamed = {signal: value for signal, _, value in nodes.writes}
    assert fields["PeakForce"] == streamed["JoiningForcePeak"]
    assert fields["JoiningDistance"] == streamed["JoiningDistance"]


@pytest.mark.asyncio
async def test_s2_records_the_curve_and_draws_the_distance_from_the_stop() -> None:
    """§3.4a's whole reason for storing a curve, and Task 2's reason for deleting
    `distance_of`.

    `JoiningDistance` is the hard stop the ram runs to on every part, drawn against the
    position sensor's own noise -- not read off the trace, because nothing happening at
    constant ram position can appear in a force-against-position trace. So it stays
    within a few sigma of nominal while the contact point moves, which is exactly what
    makes M2c's scenario 7 point at the wrong cause. The peak, by contrast, IS read off
    the trace.
    """
    settings = Settings()
    station, nodes = build_one(JoiningStation)
    await station.run_cycle(T0, Carrier(0), loaded())
    fields = nodes.payloads(PART_PROCESSED)[0]

    curve = fields["Curve"]
    assert isinstance(curve, list)
    assert len(curve) == settings.curve_samples
    assert fields["PeakForce"] == pytest.approx(round(peak_of(tuple(curve)), 2))

    distance = fields["JoiningDistance"]
    assert isinstance(distance, float)
    # The stop, not the trace: five sigma of the position sensor either side of it.
    assert abs(distance - settings.joining_distance_nominal) < (
        5 * settings.joining_distance_sigma
    )
    # And the statistic the two scalars cannot produce is computable from what was
    # stored -- which is the whole of why D6 gives the curve a table of its own.
    assert work_of(tuple(curve), settings) > 0.0


@pytest.mark.asyncio
async def test_s4_refuses_a_part_whose_serial_it_never_saw() -> None:
    """M2a's S4 refuses a part with no disposition. Identity is held to the same
    standard: sorting a part nobody can name is a quiet wrong answer, and §14's
    traceability line ends at this event."""
    station, _ = build_one(OutfeedStation)
    unnamed = PartState(disposition="good")
    with pytest.raises(ValueError, match="no assembly"):
        await station.run_cycle(T0, Carrier(0), unnamed)


@pytest.mark.asyncio
async def test_s4_reports_the_disposition_and_the_reason_s3_decided() -> None:
    """§5.2's `part_dispositions` row. The reason is S3's verdict carried on the part:
    re-deriving it here would mean asking which inspection result belongs to this
    serial, which is the join §3.4a forbids one station earlier."""
    station, nodes = build_one(OutfeedStation)
    part = loaded()
    part.disposition = "reject"
    part.reason = "gap"
    await station.run_cycle(T0, Carrier(0), part)

    assert part.assembly is not None
    assert nodes.payloads(PART_COMPLETED) == [
        {
            "AssemblySerial": part.assembly.serial,
            "Disposition": "reject",
            "Reason": "gap",
        }
    ]


@pytest.mark.asyncio
async def test_a_part_carries_its_serial_from_s1_to_s4() -> None:
    """The whole milestone in one test, on the real line rather than on four stations
    driven by hand: S1 names the part, and the same name comes back out of S2's press,
    S3's verdict and S4's disposition.

    Asserted as prefixes of S1's own sequence rather than as set membership. The
    buffers are FIFO, so each station downstream has seen a prefix of what S1 created,
    in that order -- and a station that emitted the right *set* of serials in the wrong
    order would be one whose events were attributed to the wrong parts, which
    set membership cannot see.
    """
    line, _clock, nodes = await build_running_line()
    for _ in range(200):
        await line.step()

    serials = {
        event.station: [
            fields["AssemblySerial"] for fields in nodes[event.station].payloads(event)
        ]
        for event in (
            ASSEMBLY_CREATED,
            PART_PROCESSED,
            INSPECTION_RESULT,
            PART_COMPLETED,
        )
    }
    created = serials["S1_Feeding"]
    assert len(serials["S4_Outfeed"]) >= 3, (
        "no part reached S4, so this proved nothing about carrying a serial"
    )
    for station in ("S2_Joining", "S3_Inspection", "S4_Outfeed"):
        seen = serials[station]
        assert seen == created[: len(seen)], station
