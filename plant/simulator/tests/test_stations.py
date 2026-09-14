"""The four stations' own behaviour. Node writes are captured through a recording
double for the address space, so these assert what each station *records*, never how
it reaches asyncua."""

from __future__ import annotations

import ast
import itertools
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from conftest import RecordingNodes
from simulator.address_space import PACKML_SIGNALS, STATION_SIGNALS
from simulator.carriers import Carrier
from simulator.config import Settings
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

# §4.1's browse names, which is what the real address space gives
# `StationNodes.code` and what `Settings.station_takt_seconds` is keyed by.
_CODES: dict[type[Station], str] = {
    FeedingStation: "S1_Feeding",
    JoiningStation: "S2_Joining",
    InspectionStation: "S3_Inspection",
    OutfeedStation: "S4_Outfeed",
}


def build_one(
    factory: type[Station], always_reject: bool | None = None
) -> tuple[Station, RecordingNodes]:
    """Each station is constructed exactly as Task 7 constructs it, minus the real
    address space."""
    settings = Settings()
    nodes = RecordingNodes(_CODES[factory])
    if factory is InspectionStation:

        async def produce(_serial: str, _at: datetime) -> PartOutcome:
            reject = bool(always_reject)
            return PartOutcome(
                disposition="reject" if reject else "good",
                defect_class="gap" if reject else None,
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
    """
    for station, nodes in build_all():
        # S4 refuses a part nobody inspected, so every station is handed the part it
        # would really receive: only S4's predecessor has already stamped one.
        outfeed = station.code == _CODES[OutfeedStation]
        part = PartState(disposition="good") if outfeed else PartState()
        await station.run_cycle(T0, Carrier(0), part)
        declared = {name for name, _ in PACKML_SIGNALS + STATION_SIGNALS[station.code]}
        assert nodes.signals() <= declared, station.code
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
        part = PartState(disposition="good") if outfeed else PartState()
        await station.run_cycle(T0, Carrier(0), part)
        counters = (
            {"GoodCount", "RejectCount"} if outfeed else {station.part_count_signal}
        )
        assert counters <= nodes.signals(), station.code


@pytest.mark.asyncio
async def test_part_count_is_monotonic_across_cycles() -> None:
    station, nodes = build_one(FeedingStation)
    for _ in range(3):
        await station.run_cycle(T0, Carrier(0), PartState())
    counts = [value for signal, _, value in nodes.writes if signal == "PartCount"]
    assert counts == [1, 2, 3]


@pytest.mark.asyncio
async def test_feeding_records_both_lane_fills() -> None:
    """Two lanes, not one level published under two names. S1 alternates feeders, so
    after the first part lane 1 has supplied it and lane 2 has supplied nothing, and
    lane 1 stands `lane_draw_per_part` lower.

    Asserting only that both names were written is what let an implementation that
    drained both lanes by the whole part count pass -- and M2c's scenario 5
    contaminates exactly one lane, which that implementation cannot express. The names
    do not distinguish the two; the levels do.
    """
    # Measurement noise off, because the claim is about the lanes rather than about a
    # draw: at the shipped `lane_fill_sigma` the 0.5 the lanes differ by is inside one
    # sigma of the noise on each, so a run of this test would sometimes be reading the
    # Gaussian instead of the sawtooth.
    nodes = RecordingNodes(_CODES[FeedingStation])
    station = FeedingStation(nodes, Settings(lane_fill_sigma=0.0), seed=1)

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
    assert levels["LaneFill_2"] - levels["LaneFill_1"] == pytest.approx(
        Settings().lane_draw_per_part
    )


@pytest.mark.asyncio
async def test_joining_records_a_peak_force_and_a_distance() -> None:
    """§4.1's two S2 process signals. The force-distance curve behind them is M2b."""
    station, nodes = build_one(JoiningStation)
    await station.run_cycle(T0, Carrier(0), PartState())
    assert {"JoiningForcePeak", "JoiningDistance"} <= nodes.signals()


@pytest.mark.asyncio
async def test_inspection_puts_its_verdict_on_the_part() -> None:
    """S4 sorts on this. Without it GoodCount and RejectCount would have to be
    reconstructed by time-joining, which is the inference §3.4a forbids."""
    station, nodes = build_one(InspectionStation)
    part = PartState()
    await station.run_cycle(T0, Carrier(0), part)
    assert part.disposition in ("good", "reject")
    assert len(nodes.events) == 1


@pytest.mark.asyncio
async def test_only_rejects_carry_an_image() -> None:
    """§3.4, and it is what keeps images inside the single permitted channel."""
    station, nodes = build_one(InspectionStation, always_reject=True)
    await station.run_cycle(T0, Carrier(0), PartState())
    assert nodes.events[0][1]["Image"]

    good, good_nodes = build_one(InspectionStation, always_reject=False)
    await good.run_cycle(T0, Carrier(0), PartState())
    assert not good_nodes.events[0][1]["Image"]


@pytest.mark.asyncio
async def test_outfeed_counts_good_and_reject_separately() -> None:
    station, nodes = build_one(OutfeedStation)
    await station.run_cycle(T0, Carrier(0), PartState(disposition="good"))
    await station.run_cycle(T0, Carrier(1), PartState(disposition="reject"))
    await station.run_cycle(T0, Carrier(2), PartState(disposition="reject"))

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
        await station.run_cycle(T0, Carrier(0), PartState())


@pytest.mark.asyncio
async def test_a_state_change_is_written_with_its_reason() -> None:
    station, nodes = build_one(FeedingStation)
    await station.publish_state(T0, State.SUSPENDED, "starved:carrier-return")
    assert ("State", T0, "Suspended") in nodes.writes
    assert ("StateReason", T0, "starved:carrier-return") in nodes.writes


def test_each_station_takes_its_own_nominal_takt() -> None:
    """§3.1: S3 paces the line and the stations above it run faster, so their buffers
    fill. One shared takt would leave every buffer oscillating between empty and one,
    and buffer capacity would bound nothing."""
    means = {}
    for factory in (FeedingStation, JoiningStation, InspectionStation, OutfeedStation):
        station, _ = build_one(factory)
        draws = [station.next_takt() for _ in range(500)]
        means[station.code] = sum(draws) / len(draws)

    assert means["S1_Feeding"] < means["S2_Joining"] < means["S3_Inspection"]
    assert means["S3_Inspection"] == pytest.approx(means["S4_Outfeed"], abs=0.05)


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
        FeedingStation(RecordingNodes("S1_Feeding"), settings, seed=1)


_DRAW_PROBE = """
from simulator.config import Settings
from simulator.stations import FeedingStation


class Nodes:
    code = "S1_Feeding"

    async def write(self, signal, at, value):
        raise AssertionError("the probe never cycles")

    async def trigger_event(self, at, fields):
        raise AssertionError("the probe never cycles")


settings = Settings()
station = FeedingStation(Nodes(), settings, seed=settings.seed)
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


def test_successive_takts_never_repeat() -> None:
    """Not realism: asyncua's monitored-item filter drops a notification whenever the
    written value is unchanged, so a repeated takt is a row that never reaches the
    historian. D13 deletes this guard in M2c, once the noise floor makes takt vary
    for real."""
    station, _ = build_one(FeedingStation)
    values = [station.next_takt() for _ in range(200)]
    assert all(a != b for a, b in itertools.pairwise(values))
