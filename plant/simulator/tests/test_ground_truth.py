"""§3.6's ground-truth log: what it records, and that two runs of one scenario write the
same bytes.

**The determinism proof is the point of this file.** A byte-identical log across two runs
is cheap and very strong: it covers every draw in the plant at once, because an unseeded
one anywhere -- a `hash()`-derived key, a `random` call outside the seeded streams, a
dict iteration over something built from a set -- moves a part's truth or its instant and
the two files disagree. An earlier milestone shipped a `PYTHONHASHSEED`-salted seed that
this would have caught on the first run.

Nothing here asserts that a fault was injected. The injection records are read back to
check that the log says what the scenario says, which is a claim about the log; whether
the consequences appeared is what `test_scenarios` measures on the line itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from conftest import build_running_line, new_clock
from simulator.clock import SimulatedClock
from simulator.config import ClockConfig, Settings
from simulator.faults import NO_FAULTS
from simulator.ground_truth import (
    INJECTION,
    NO_SCENARIO,
    PART,
    RUN,
    GroundTruthLog,
    open_log,
    recording,
    run_id_for,
)
from simulator.identity import LANES
from simulator.inspection_client import DEFECT_CLASSES, InspectionClient
from simulator.scenarios import scenario

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


def _fast(seed: int | None = None) -> Settings:
    """The shipped settings with small frames.

    Nothing here reads a pixel: the log records what the plant decided about a part, and
    the render is in the path only so that `produce` is exercised as it ships rather than
    through a shortcut around it. At the shipped 320x240 this file would spend most of
    its time in PNG compression.
    """
    if seed is None:
        return Settings(image_width=64, image_height=64)
    return Settings(seed=seed, image_width=64, image_height=64)


def _inspection_service() -> httpx.MockTransport:
    """A stand-in for the inspection service that answers everything `produce` asks.

    The verdict it returns is deliberately uninteresting: this file is about the plant's
    own declaration of truth, which is written before the request is ever made, and a
    classifier's opinion has no business deciding what ground truth records.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={
                "disposition": "good",
                "defect_class": None,
                "confidence": 0.9,
                "confidences": {name: 0.03 for name in DEFECT_CLASSES},
                "model_version": "test-1",
            },
        )

    return httpx.MockTransport(handler)


async def _run(
    path: Path, number: int | None, seconds: float, settings: Settings
) -> None:
    """Run a real line for `seconds` of simulated time, writing its ground truth to
    `path`.

    The clock is built from a fixed instant rather than from the wall clock, because two
    runs that began at two different instants would place their scenarios on two
    different origins and the byte comparison below would be asserting the wall clock.
    """
    clock = SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=1), catchup_speed=700.0),
        wall_fn=lambda: T0,
    )
    chosen = scenario(number, settings) if number else None
    faults = chosen.fault_set(clock.history_start) if chosen else NO_FAULTS

    with open_log(path, settings, clock, chosen) as log:
        async with httpx.AsyncClient(transport=_inspection_service()) as http:
            client = InspectionClient(settings, http, faults=faults)
            line, clock, _nodes = await build_running_line(
                settings,
                faults=faults,
                produce=recording(log, client),
                clock=clock,
            )
            horizon = clock.history_start + timedelta(seconds=seconds)
            while (due := line.next_due) is not None and due < horizon:
                await line.step()


def _records(path: Path, kind: str) -> list[dict[str, object]]:
    return [
        record
        for record in (json.loads(line) for line in path.read_text().splitlines())
        if record["record"] == kind
    ]


# --- what the log carries -------------------------------------------------------------


def test_the_header_carries_the_seed_the_scenario_and_the_clock(
    tmp_path: Path,
) -> None:
    """§3.6 names all three, and each is one a reader needs to reproduce the run.

    `carrier_count` is there beside the seed because it is the one setting that changes
    what the seed means: the carrier qualities are normalised across the pool, so
    changing the count redraws every one of them.
    """
    settings = Settings()
    clock = new_clock(settings)
    chosen = scenario(7, settings)

    path = tmp_path / "header.jsonl"
    with GroundTruthLog(path, run_id_for(settings, clock, chosen.number)) as log:
        log.record_run(settings, clock, chosen)
    header = _records(path, RUN)[0]

    assert header["seed"] == settings.seed
    assert header["carrier_count"] == settings.carrier_count
    assert header["scenario"] == 7
    clock_record = header["clock"]
    assert isinstance(clock_record, dict)
    assert clock_record["history_start"] == clock.history_start.isoformat()
    assert clock_record["history_depth_seconds"] == clock.history_depth.total_seconds()
    assert clock_record["catchup_speed"] == clock.catchup_speed


@pytest.mark.asyncio
async def test_every_injection_is_recorded_with_its_expected_consequences(
    tmp_path: Path,
) -> None:
    """**The field that lets M2c assert without diagnosing.** Scenario 1 records "S2,
    then S3, then S4, suspended and starved, in that order, within N s", and Task 7
    asserts that sequence appears in `state_changes` -- not that anything found it.

    The instants are simulated and absolute: `faults.Fault` carries offsets, because a
    scenario written against a wall clock could be run once, and the log carries the
    instants those offsets landed on, because `state_changes` and `inspection_results`
    carry nothing but instants and the two have to be comparable.
    """
    settings = _fast()
    path = tmp_path / "gt.jsonl"
    await _run(path, 1, settings.scenario_warmup_seconds + 300.0, settings)

    injections = _records(path, INJECTION)
    chosen = scenario(1, settings)
    assert len(injections) == len(chosen.injections)

    recorded = injections[0]
    injected = chosen.injections[0]
    assert recorded["kind"] == str(injected.fault.kind)
    assert recorded["params"] == dict(injected.fault.params)

    consequences = recorded["consequences"]
    assert isinstance(consequences, list)
    first = consequences[0]
    assert isinstance(first, dict)
    assert first["observable"] == "state_changes"
    assert first["expect"] == "suspended_in_order"
    assert first["subjects"] == ["S2_Joining", "S3_Inspection", "S4_Outfeed"]
    assert first["within_seconds"] == injected.consequences[0].within_seconds

    # And the instants are the offsets placed on the run's own origin, which is what a
    # reader joins against.
    header = _records(path, RUN)[0]
    clock_record = header["clock"]
    assert isinstance(clock_record, dict)
    origin = datetime.fromisoformat(str(clock_record["history_start"]))
    assert recorded["at"] == (origin + injected.fault.at).isoformat()
    assert injected.fault.until is not None
    assert recorded["until"] == (origin + injected.fault.until).isoformat()


@pytest.mark.asyncio
async def test_every_part_the_line_inspected_has_its_true_defect_state(
    tmp_path: Path,
) -> None:
    """§3.6: the true defect state of **every** part, so false accepts and false rejects
    can be scored against it.

    The plant's own declaration, not the classifier's -- the stand-in service above
    reports every part good, and the log still records the ones that are not. A log
    written from the verdict would score the classifier against itself.

    **And each defect carries the lane its component came from**, which exists nowhere
    else: `truth_for` drops the lane because the classifier scores a class and not a
    component, and every assembly draws from both lanes, so no record downstream can ever
    recover it. §3.5's scenario 5 is "clean lane 2"; without this, a later milestone
    grading that answer would be grading a guess against something nothing wrote down.
    """
    settings = _fast()
    path = tmp_path / "gt.jsonl"
    await _run(path, None, 3600.0, settings)

    parts = _records(path, PART)
    assert len(parts) > 100, "the line inspected almost nothing, so this proves nothing"
    assert len({str(part["part_id"]) for part in parts}) == len(parts)
    assert _records(path, RUN)[0]["scenario"] == NO_SCENARIO
    assert not _records(path, INJECTION)

    lanes: set[int] = set()
    for part in parts:
        defects = part["defects"]
        assert isinstance(defects, list)
        assert 0 <= int(str(part["carrier_id"])) < settings.carrier_count
        for defect in defects:
            assert isinstance(defect, dict)
            assert str(defect["class"]) in DEFECT_CLASSES
            lanes.add(int(str(defect["lane"])))
    defective = [part for part in parts if part["defects"]]
    assert defective, (
        "not one part in the log carries a defect while the baseline scrap rate is "
        f"{settings.reject_rate:.1%}: the truth is not reaching the log"
    )
    assert lanes <= set(LANES)
    assert lanes == set(LANES), (
        f"every defect in the log came off lane(s) {sorted(lanes)}: §4.1 gives S1 lanes "
        f"{LANES} and the draw is per (lane, class), so a log that only ever names one "
        "is a log whose lane is a constant rather than a fact"
    )


# --- determinism ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_is_reproducible_byte_for_byte_from_its_seed(
    tmp_path: Path,
) -> None:
    """§3.6: the same seed plus the same scenario reproduces the run exactly.

    **Byte-identical, which is the cheapest very strong assertion available here.** It
    covers every draw in the plant at once -- the carrier qualities, the per-part scrap
    draws, the takt jitter, the micro-stops, the lot suppliers, the press -- because an
    unseeded one anywhere moves either a part's truth or the instant it was inspected at,
    and both are in these bytes. It is what would have caught this repository's own
    `PYTHONHASHSEED`-salted seed on the first run rather than on the second boot.

    Two whole runs of a four-station line rather than two calls to one function: the
    property is about the plant, and a comparison of two `truth_for` calls would pass on
    a line whose queue ordering had become nondeterministic.
    """
    settings = _fast()
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    for path in (first, second):
        await _run(path, 7, settings.scenario_warmup_seconds + 1200.0, settings)

    assert first.read_bytes() == second.read_bytes()
    # And the run is not trivially short: a byte comparison of two headers would pass on
    # a plant that produced nothing at all.
    assert len(_records(first, PART)) > 100


@pytest.mark.asyncio
async def test_a_different_seed_writes_a_different_log(tmp_path: Path) -> None:
    """The other half, and the one that makes the equality above mean something: a
    comparison that passed whatever the plant did would pass on a log that recorded
    nothing about the parts."""
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    seconds = Settings().scenario_warmup_seconds + 600.0
    await _run(path_a, 7, seconds, _fast())
    await _run(path_b, 7, seconds, _fast(seed=Settings().seed ^ 1))

    assert path_a.read_bytes() != path_b.read_bytes()


def test_the_run_id_is_derived_from_what_determines_the_run() -> None:
    """A random id would make two identical runs look like two different ones, and would
    make the byte-identity claim above uncheckable. Seeded with `zlib.crc32` and never
    `hash()`, which is salted per interpreter process."""
    settings = Settings()
    clock = new_clock(settings)
    assert run_id_for(settings, clock, 7) == run_id_for(settings, clock, 7)
    assert run_id_for(settings, clock, 7) != run_id_for(settings, clock, 3)
    assert run_id_for(settings, clock, 7) != run_id_for(
        Settings(seed=settings.seed ^ 1), clock, 7
    )
