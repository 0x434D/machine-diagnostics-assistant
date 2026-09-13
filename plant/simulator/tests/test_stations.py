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

_CODES: dict[type[Station], str] = {
    FeedingStation: "S1",
    JoiningStation: "S2",
    InspectionStation: "S3",
    OutfeedStation: "S4",
}


class RecordingNodes:
    """Stands in for StationNodes. Records (signal, timestamp, value) per write and
    (timestamp, fields) per event triggered."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.writes: list[tuple[str, datetime, float | str]] = []
        self.events: list[tuple[datetime, dict[str, object]]] = []

    async def write(self, signal: str, at: datetime, value: float | str) -> None:
        self.writes.append((signal, at, value))

    async def trigger_event(self, at: datetime, fields: dict[str, object]) -> None:
        self.events.append((at, fields))

    def signals(self) -> set[str]:
        return {signal for signal, _, _ in self.writes}


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
async def test_every_station_records_its_takt_and_its_part_count() -> None:
    """§4.1 gives all four the same two, and they are deliberately different in
    kind -- a noisy float where a deadband is meaningful, and a monotonic counter
    where one would silently lose parts."""
    for station, nodes in build_all():
        # S4 refuses a part nobody inspected, so every station is handed the part it
        # would really receive: only S4's predecessor has already stamped one.
        part = PartState(disposition="good") if station.code == "S4" else PartState()
        await station.run_cycle(T0, Carrier(0), part)
        assert {"TaktTime", "PartCount"} <= nodes.signals()


@pytest.mark.asyncio
async def test_part_count_is_monotonic_across_cycles() -> None:
    station, nodes = build_one(FeedingStation)
    for _ in range(3):
        await station.run_cycle(T0, Carrier(0), PartState())
    counts = [value for signal, _, value in nodes.writes if signal == "PartCount"]
    assert counts == [1, 2, 3]


@pytest.mark.asyncio
async def test_feeding_records_both_lane_fills() -> None:
    station, nodes = build_one(FeedingStation)
    await station.run_cycle(T0, Carrier(0), PartState())
    assert {"LaneFill_1", "LaneFill_2"} <= nodes.signals()


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

    assert means["S1"] < means["S2"] < means["S3"]
    assert means["S3"] == pytest.approx(means["S4"], abs=0.05)


_DRAW_PROBE = """
from simulator.config import Settings
from simulator.stations import FeedingStation


class Nodes:
    code = "S1"

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
