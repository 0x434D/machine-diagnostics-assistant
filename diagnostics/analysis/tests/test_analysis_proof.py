"""§1.5's proof: a cause, computed from a history that names none.

§8.4 puts this suite above the LLM evaluation and warns that it is the one most easily
forgotten: *"inject a known fault, assert the computed chain against ground truth."* This
is that suite for M3 — §3.5's eight scenarios, each seeded as the **observable
consequences** it declares, each answered by the endpoints M3 ships, with no model
anywhere in the loop.

**The seeding rule is the whole of what makes the numbers worth anything.** M2c's
`scenarios.py` declares twelve consequences per §3.5's rows and deliberately names no
cause — its own docstring says writing the cause there "would make every one of M7's
numbers circular". So every fixture below is built from the consequence vocabulary and
never from the injection: a sequence of `Suspended` transitions with the buffer each one
names, a class rate that rises on one carrier, a stream that falls or does not, a lot
whose parts gap. What the analysis then computes — the root station, the category, the
significant dimension — is the measurement, and nothing in the fixture was allowed to
know it in advance.

**The ground-truth log is not read here and cannot be.** It lives on a volume no
diagnostics container mounts (`test_compose_invariants.py` enforces that), and a proof
that read it would be scoring the analysis against an answer it had been handed.

**The noise floor is §3.5's own, because without it half of these scenarios are a
`GROUP BY`.** The carrier pool, the line's reject rate and the per-class draws are
reproduced from the plant's *measurements* rather than imported — the two workspaces
share no code (§2.1) — and each constant below cites where it was measured.

Marked `authenticity` and so run by `make verify` rather than by `make check`: the suite
needs no container of its own beyond the Postgres the analysis tests already start, and
the whole of it is seconds rather than minutes. `measurements/authenticity/README.md`
carries the argument for where it belongs.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import Connection

from tests.conftest import (
    BUFFER_CAPACITY,
    DEFECT_CLASSES,
    TAKT,
    _buffer_ids,
    _station_ids,
)

pytestmark = pytest.mark.authenticity


# --- §3.5's plant, as the plant measured it ---------------------------------------------

PROOF_START = datetime(2026, 9, 10, 16, 45, tzinfo=UTC)
"""Where every scenario's history begins.

**Quarter to the hour, and that is load-bearing for scenario 7.** `/inspection/stats` and
the time-bucket pattern dimension cut the window into hours aligned to the clock in UTC, so
a window opening on the hour puts §3.5's 500-part lot almost entirely inside one bucket and
the period then carries the lot's whole signal. Opening at :45 puts a bucket boundary
through the middle of that lot, which is the arrangement in which "the defects follow the
lot" and "the defects follow the clock" are different claims — and scenario 7 is the row
that turns on telling them apart.
"""

CARRIER_COUNT = 18
"""`simulator.config.carrier_count`."""

WORN_CARRIER = 7
"""`simulator.config.worn_carrier_id`. A carrier *id*, so the pool is 1..18."""

LINE_REJECT_RATE = 0.015
"""`simulator.config.reject_rate`."""

DEFECT_DRAWS = 2 * len(DEFECT_CLASSES)
"""`len(LANES) * len(DEFECT_CLASSES)` — the independent draws `simulator.noise` makes per
part, one per (lane, class), and not the size of the defect vocabulary."""

PER_DRAW_RATE = -math.expm1(math.log1p(-LINE_REJECT_RATE) / DEFECT_DRAWS)
"""`simulator.noise.class_propensity`: the per-draw rate at which a nominal carrier's part
carries at least one defect exactly `reject_rate` of the time."""

POOL_RELATIVE_SPREAD = 0.287
"""How widely the eighteen carriers actually scrap, relative to their own mean.

`test_noise` measures the shipped pool at **28.7 %** and notes that the distribution it is
drawn from is 36.2 % — "eighteen draws is a small sample and this is the spread the line
actually has". The realised number is the one a proof about that line has to use: the wider
one describes a pool the plant does not have and makes every carrier comparison harder than
the real one.
"""

WORN_CARRIER_BASELINE = 1.34
"""Where carrier 7 sat in that pool *before* anything was injected.

`test_noise` measures the worn carrier's `misalignment | scratch` rate at **2.18 %** against
the pool's **0.571 %**, and the wear itself is `exp(3 × 0.35) = 2.86` — so the carrier the
plant wears was already at 1.34 × the pool's baseline when the run started.

**Stated rather than left to a draw, because the scenario turns on it.** Placed at the
pool's median instead, carrier 7's wear lifts its *reject* rate by 1.6 × inside a pool whose
own qualities span 3 ×, and `/inspection/patterns` cannot separate it at any depth —
measured, and recorded in `measurements/authenticity/README.md`. Which of the two §3.5 ships
is a fact about the seed, and this is the one it shipped.
"""

WEAR_FACTOR = math.exp(3.0 * 0.35)
"""`simulator.config.carrier_wear_factor` = exp(carrier_wear_sigmas × log sigma) = 2.86."""

WEAR_CLASSES = ("misalignment", "scratch")
"""`faults.DEFECT_CLASSES_BY_KIND[CARRIER_WEAR]`, and §3.5 row 4's two subjects."""

CONTAMINATION_CLASSES = ("missing_part", "contamination")
CONTAMINATION_FACTOR = 6.0
"""`simulator.config.lane_contamination_factor`, on the contaminated lane's draws only."""

CONTAMINATED_LANE = 2
"""`simulator.config.contaminated_lane`."""

OPTICS_CLARITY = 0.55
"""`simulator.config.optics_fouling_factor`: how much of the camera's contrast survives."""

FOULING_RAMP_PARTS = 1000
"""Over how many parts the fouling reaches its final clarity. §3.5 ramps it in seconds; at
the line's takt this is the same statement in the unit the fixture counts in."""

LOT_SIZE = 500
"""`simulator.config.lot_size`: components per supplier lot, so parts per lot per lane."""

LANE_STAGGER = LOT_SIZE // 2
"""How far lane 2's lot boundaries sit from lane 1's.

`simulator.config.lot_stagger_fraction`'s reason, in this fixture's units: without it both
lanes roll their lots on the same part, every lot covers the same part set as its opposite
number, and "which lot" carries no more information than "when".
"""

BAD_LOT_LANE = 1
BAD_LOT_INDEX = 1
"""`simulator.config.bad_lot_lane` and `bad_lot_index`: §3.5 row 7's lot."""

GAP = "gap"

UNDERSIZED_FACTOR = 22.0
"""What §3.5 row 7's 0.6 mm of undersize does to the `gap` draw on the affected lane.

Expressed as a multiplier on the draw rather than in millimetres because the fixture seeds
the *consequence* and not the press: `test_scenario_consequences` measures the shipped run's
bad lot at **14 gapped parts in 500** against a 0.2706 % baseline outside it, and this is the
factor that lands there — the fixture gaps 16 of its 500.
"""

DEFECTIVE_COMPONENT_PART = 777
"""Which part §3.5 row 8's single defective component goes into.

Forced rather than drawn: row 8's fault is 2.0 mm of undersize on one component, which is
`defective_component_mm` against row 7's 0.6, and that part gaps with certainty. A draw
would make "exactly one part" a probability, which is the one thing this row is about.
"""

JOINING_FORCE_NOMINAL = 4200.0
JOINING_FORCE_SIGMA = 40.0
JOINING_FORCE_DRIFT = -420.0
JOINING_FORCE_SIGNAL = "JoiningForcePeak"
"""`simulator.config`'s clamp, its part-to-part spread and §3.5 row 3's drift, and the §4.1
stream `scenarios.py` names as row 3's and row 7's observable."""

FORCE_DRIFT_RAMP_PARTS = 450
"""Over how many parts the drifted clamp reaches its full offset. `joining_force_drift_ramp_
seconds` at the line's takt."""

MICRO_STOP_FLOOR = timedelta(seconds=9)
"""`stops.DEFAULT_INTERRUPTION_FLOOR`, restated here for one purpose: the fixture's window
edges must sit within it of the first and last part out, or a scenario with nothing wrong
with it reports a micro-stop against its own boundary."""


# --- the population ---------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """One scenario's declared consequences, as what they do to the noise floor.

    Every field names a §3.5 row and nothing names a cause: `worn_carrier` is "these two
    classes rise on this carrier", `bad_lot` is "these parts gap", `force_drift_from` is
    "this stream falls". A field called `fault` would be the circularity M2c's
    `scenarios.py` refuses, one milestone later.
    """

    parts: int
    worn_carrier: int | None = None
    contaminated: bool = False
    fouled_from: int | None = None
    bad_lot: range | None = None
    defective_part: int | None = None
    force_drift_from: int | None = None


@dataclass(frozen=True)
class Part:
    """One part as the plant would have published it: a carrier, two lots, a verdict."""

    index: int
    carrier: int
    lots: tuple[str, str]
    classes: frozenset[str]
    scores: tuple[float, ...]

    @property
    def serial(self) -> str:
        return f"A-{self.index:08d}"

    @property
    def rejected(self) -> bool:
        """§3.4's verdict, which is the truth about the part and not a reading of the
        scores: §3.5 row 6 veils the scores without moving one verdict, and a fixture that
        derived the verdict from the score could not seed that row at all."""
        return bool(self.classes)


def _carrier_qualities() -> dict[int, float]:
    """The eighteen carriers' standing defect multipliers, mean 1.

    Taken at the lognormal's own quantiles rather than drawn, so the pool has exactly the
    spread `test_noise` measured and no seed's luck on top of it — and so that the one
    carrier §3.5 wears can be placed where the shipped run placed it rather than wherever a
    draw happened to put it.
    """
    sigma = math.sqrt(math.log1p(POOL_RELATIVE_SPREAD**2))
    normal = statistics.NormalDist()
    raw = sorted(
        math.exp(sigma * normal.inv_cdf((rank + 0.5) / CARRIER_COUNT))
        for rank in range(CARRIER_COUNT)
    )
    mean = statistics.fmean(raw)
    ranked = [quality / mean for quality in raw]
    worn_rank = min(
        range(CARRIER_COUNT), key=lambda rank: abs(ranked[rank] - WORN_CARRIER_BASELINE)
    )
    others = (quality for rank, quality in enumerate(ranked) if rank != worn_rank)
    qualities = {WORN_CARRIER: ranked[worn_rank]}
    for carrier in range(1, CARRIER_COUNT + 1):
        if carrier != WORN_CARRIER:
            qualities[carrier] = next(others)
    return qualities


CARRIER_QUALITIES = _carrier_qualities()


def _lots_of(index: int) -> tuple[str, str]:
    """Which lot each feeder lane was drawing from when part `index` was built."""
    return (
        f"L-1-{index // LOT_SIZE:02d}",
        f"L-2-{(index + LANE_STAGGER) // LOT_SIZE:02d}",
    )


def _multiplier(shape: Shape, index: int, carrier: int, lane: int, cls: str) -> float:
    """What this scenario does to one (lane, class) draw of one part."""
    if shape.worn_carrier == carrier and cls in WEAR_CLASSES:
        return WEAR_FACTOR
    if (
        shape.contaminated
        and lane == CONTAMINATED_LANE
        and cls in CONTAMINATION_CLASSES
    ):
        return CONTAMINATION_FACTOR
    if (
        shape.bad_lot is not None
        and index in shape.bad_lot
        and lane == BAD_LOT_LANE
        and cls == GAP
    ):
        return UNDERSIZED_FACTOR
    return 1.0


def _clarity(shape: Shape, index: int) -> float:
    """How much of the camera's contrast reached the frame for part `index` (§3.5 row 6)."""
    if shape.fouled_from is None or index < shape.fouled_from:
        return 1.0
    progress = min(1.0, (index - shape.fouled_from) / FOULING_RAMP_PARTS)
    return 1.0 - progress * (1.0 - OPTICS_CLARITY)


def _build(name: str, shape: Shape) -> list[Part]:
    """Every part of one scenario's run.

    One `random.Random` per part rather than one per run, the property
    `simulator.noise.scrap_draws` rests on: what a part draws does not depend on how many
    were asked about before it, so two shapes over the same parts differ only where their
    multipliers differ. Scenarios 7 and 8 are seeded that way on purpose — one bad lot
    against one bad component, and nothing else between them.
    """
    parts: list[Part] = []
    for index in range(shape.parts):
        carrier = index % CARRIER_COUNT + 1
        rng = random.Random(f"{name}:{index}")
        quality = CARRIER_QUALITIES[carrier]
        present = {
            cls
            for cls in DEFECT_CLASSES
            for lane in (1, 2)
            if rng.random()
            < PER_DRAW_RATE * quality * _multiplier(shape, index, carrier, lane, cls)
        }
        if shape.defective_part == index:
            present.add(GAP)
        clarity = _clarity(shape, index)
        scores = tuple(
            clarity
            * (rng.uniform(0.55, 0.95) if cls in present else rng.uniform(0.01, 0.08))
            for cls in DEFECT_CLASSES
        )
        parts.append(Part(index, carrier, _lots_of(index), frozenset(present), scores))
    return parts


def _inspected_at(index: int) -> datetime:
    return PROOF_START + index * TAKT


def _left_at(index: int) -> datetime:
    """When part `index` was dispositioned at S4, which is what §5.4 defines a stop by."""
    return PROOF_START + (index + 1) * TAKT


def _window(shape: Shape) -> dict[str, str]:
    """The half-open window holding every part of this run.

    One takt past the last part out, so the window's own trailing edge is closer to it than
    the interruption floor. A window that ended on the last part would leave a takt of
    silence behind it and `/stops` would count the fixture's boundary as a micro-stop.
    """
    to_ts = _left_at(shape.parts - 1) + TAKT
    assert to_ts - _left_at(shape.parts - 1) <= MICRO_STOP_FLOOR
    return {"from": PROOF_START.isoformat(), "to": to_ts.isoformat()}


# --- writing it into §5.2's tables ------------------------------------------------------


def _write_parts(
    conn: Connection, parts: Sequence[Part], missing_output: Iterable[int]
) -> None:
    """The per-part half of §5.2, in the order the foreign keys need it.

    `missing_output` is the parts that were inspected and never dispositioned — which is
    how a stop is seeded, because §5.4 defines a stop as the *absence* of output and the
    only faithful way to seed an absence is to take the output away.
    """
    stations = _station_ids(conn)
    omitted = set(missing_output)
    lots = sorted({lot for part in parts for lot in part.lots})

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO carriers (id) VALUES (%s) ON CONFLICT DO NOTHING",
            [(carrier,) for carrier in range(1, CARRIER_COUNT + 1)],
        )
        cur.executemany(
            "INSERT INTO component_lots (lot_code, lane, supplier, loaded_at)"
            " VALUES (%s, %s, %s, %s)",
            [(code, int(code[2]), f"supplier-{code[2]}", PROOF_START) for code in lots],
        )
    lot_ids = {
        code: identifier
        for code, identifier in conn.execute("SELECT lot_code, id FROM component_lots")
    }

    components: list[tuple[object, ...]] = []
    genealogy: list[tuple[object, ...]] = []
    assemblies: list[tuple[object, ...]] = []
    inspections: list[tuple[object, ...]] = []
    dispositions: list[tuple[object, ...]] = []
    for part in parts:
        created_at = _inspected_at(part.index) - 2 * TAKT
        assemblies.append((part.serial, created_at, part.carrier))
        for position, (lane, lot_code) in enumerate(
            zip((1, 2), part.lots, strict=True)
        ):
            # `simulator.identity.component_serial`'s spelling: the lane is a field of the
            # serial, so a containment query can read it back off one.
            component = f"C-{lane}-{part.index:08d}"
            components.append((component, lot_ids[lot_code], lane, created_at))
            genealogy.append((part.serial, component, position))
        inspections.append(
            (
                part.serial,
                _inspected_at(part.index),
                stations["S3"],
                "reject" if part.rejected else "good",
                max(part.scores),
                part.carrier,
                list(DEFECT_CLASSES),
                list(part.scores),
            )
        )
        if part.index not in omitted:
            dispositions.append(
                (
                    part.serial,
                    _left_at(part.index),
                    "reject" if part.rejected else "good",
                    min(part.classes) if part.classes else None,
                )
            )

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO assemblies (serial, created_at, carrier_id) VALUES (%s, %s, %s)",
            assemblies,
        )
        cur.executemany(
            "INSERT INTO components (serial, lot_id, lane, read_at)"
            " VALUES (%s, %s, %s, %s)",
            components,
        )
        cur.executemany(
            "INSERT INTO genealogy (assembly_serial, component_serial, position)"
            " VALUES (%s, %s, %s)",
            genealogy,
        )
        cur.executemany(
            "INSERT INTO inspection_results (assembly_serial, source_ts, station_id,"
            " result, confidence, model_version, carrier_id, defect_classes, confidences)"
            " VALUES (%s, %s, %s, %s, %s, 'sim-1', %s, %s, %s)",
            inspections,
        )
        cur.executemany(
            "INSERT INTO part_dispositions (assembly_serial, at, disposition, reason)"
            " VALUES (%s, %s, %s, %s)",
            dispositions,
        )


def _write_force(conn: Connection, shape: Shape) -> None:
    """S2's historised clamp peak, one sample per part.

    §3.5 row 3 falls and row 7 does not, and that difference is the only thing separating
    the two from the analysis's side: both show rising `gap` defects. So both fixtures seed
    this stream, and neither one's verdict is allowed to depend on which.
    """
    stations = _station_ids(conn)
    rows: list[tuple[object, ...]] = []
    for index in range(shape.parts):
        rng = random.Random(f"force:{index}")
        offset = 0.0
        if shape.force_drift_from is not None and index >= shape.force_drift_from:
            ramp = min(1.0, (index - shape.force_drift_from) / FORCE_DRIFT_RAMP_PARTS)
            offset = ramp * JOINING_FORCE_DRIFT
        value = JOINING_FORCE_NOMINAL + offset + rng.gauss(0.0, JOINING_FORCE_SIGMA)
        rows.append((stations["S2"], JOINING_FORCE_SIGNAL, _inspected_at(index), value))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO signals (station_id, signal, source_ts, value)"
            " VALUES (%s, %s, %s, %s)",
            rows,
        )


def _write_history(
    conn: Connection,
    transitions: Sequence[
        tuple[str, datetime, str | None, str, str | None, str | None]
    ],
    levels: Sequence[tuple[str, datetime, int]],
    alarms: Sequence[
        tuple[str, str, str, int, datetime, datetime | None, datetime | None]
    ],
) -> None:
    """§3.3's state timeline, §4.1's buffer levels and the alarms beside them.

    `to_state` is filled on every transition: the raw table is reachable through no view and
    `state_changes_settled` drops a row without one, so a half-filled row here would be a
    fixture the analysis cannot see.
    """
    stations = _station_ids(conn)
    buffers = _buffer_ids(conn)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO state_changes (station_id, source_ts, from_state, to_state,"
            " reason, reason_buffer_id) VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    stations[station],
                    at,
                    from_state,
                    to_state,
                    reason,
                    None if buffer is None else buffers[buffer],
                )
                for station, at, from_state, to_state, reason, buffer in transitions
            ],
        )
        cur.executemany(
            "INSERT INTO buffer_levels (buffer_id, source_ts, level) VALUES (%s, %s, %s)",
            [(buffers[buffer], at, level) for buffer, at, level in levels],
        )
        cur.executemany(
            "INSERT INTO alarms (station_id, code, text, severity, raised_at, acked_at,"
            " cleared_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (stations[station], code, text, severity, raised, acked, cleared)
                for station, code, text, severity, raised, acked, cleared in alarms
            ],
        )


@dataclass(frozen=True)
class Seeded:
    """What a scenario fixture hands its test: the window its history covers."""

    window: dict[str, str]
    parts: tuple[Part, ...]


def _seed(
    url: str,
    name: str,
    shape: Shape,
    *,
    missing_output: Iterable[int] = (),
    transitions: Sequence[
        tuple[str, datetime, str | None, str, str | None, str | None]
    ] = (),
    levels: Sequence[tuple[str, datetime, int]] = (),
    alarms: Sequence[
        tuple[str, str, str, int, datetime, datetime | None, datetime | None]
    ] = (),
) -> Seeded:
    parts = _build(name, shape)
    with psycopg.connect(url) as conn:
        _write_parts(conn, parts, missing_output)
        _write_force(conn, shape)
        _write_history(conn, transitions, levels, alarms)
        conn.commit()
    return Seeded(_window(shape), tuple(parts))


# --- §3.5 rows 1 to 3: the chains -------------------------------------------------------

RUNNING_PARTS = 600
"""An hour of production, which is all a stop scenario needs: what these three rows claim is
a chain of transitions, and the parts are there to make the absence of output an absence."""

STOP_FIRST_MISSING = 300
STOP_LAST_MISSING = 350
"""The parts that were inspected and never left, so the line's output stops between part
299's disposition and part 350's — five minutes, bounded at both ends by a real part."""

STOP_FROM = _left_at(STOP_FIRST_MISSING - 1)
STOP_TO = _left_at(STOP_LAST_MISSING)


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


def _running(*stations: str) -> list[tuple[str, datetime, str | None, str, None, None]]:
    """Each station entering `Execute` at the head of the window.

    Every station gets one whether or not the scenario moves it again: the propagation walk
    reads a timeline, and a station with no transition at all has no episode covering the
    instant it is asked about — which would terminate a chain for want of a row that says
    the station was running normally, which is itself evidence.
    """
    return [(station, PROOF_START, None, "Execute", None, None) for station in stations]


@pytest.fixture
def scenario_1(database: str) -> Seeded:
    """Row 1: the feed above S1 runs dry and the emptiness walks down the line.

    §3.5's consequence is `SUSPENDED_IN_ORDER` over S2, S3 and S4, and the row's note adds
    that S1 itself suspends on `starved:feeder` — a StateReason the plant publishes, not a
    diagnosis: `feeder` is neither a buffer nor the carrier pool, which is exactly what makes
    it the edge of the line. **No alarm anywhere**, which `scenarios.py` calls the same
    warning in the other direction: the cause is outside the line and nothing inside it
    rings.
    """
    return _seed(
        database,
        "s1",
        Shape(parts=RUNNING_PARTS),
        missing_output=range(STOP_FIRST_MISSING, STOP_LAST_MISSING),
        transitions=[
            *_running("S1", "S2", "S3", "S4"),
            (
                "S1",
                STOP_FROM - _seconds(90),
                "Execute",
                "Suspended",
                "starved:feeder",
                None,
            ),
            (
                "S2",
                STOP_FROM - _seconds(60),
                "Execute",
                "Suspended",
                "starved:B1_2",
                "B1_2",
            ),
            (
                "S3",
                STOP_FROM - _seconds(30),
                "Execute",
                "Suspended",
                "starved:B2_3",
                "B2_3",
            ),
            ("S4", STOP_FROM, "Execute", "Suspended", "starved:B3_4", "B3_4"),
            *(
                (station, STOP_TO, "Suspended", "Execute", None, None)
                for station in ("S1", "S2", "S3", "S4")
            ),
        ],
        levels=[
            # Two samples at zero per buffer, not one: the walk reports when a buffer
            # *became* empty and stayed so, and one sample cannot tell a run from a blip.
            ("B1_2", STOP_FROM - _seconds(120), 4),
            ("B1_2", STOP_FROM - _seconds(65), 0),
            ("B1_2", STOP_FROM - _seconds(62), 0),
            ("B2_3", STOP_FROM - _seconds(120), 5),
            ("B2_3", STOP_FROM - _seconds(35), 0),
            ("B2_3", STOP_FROM - _seconds(32), 0),
            ("B3_4", STOP_FROM - _seconds(120), 6),
            ("B3_4", STOP_FROM - _seconds(5), 0),
            ("B3_4", STOP_FROM - _seconds(2), 0),
        ],
    )


@pytest.fixture
def scenario_2(database: str) -> Seeded:
    """Row 2: the outfeed below S4 blocks and the blockage walks back up the line.

    `BLOCKED_IN_ORDER` over S3, S2 and S1 — the same three buffers as row 1, the other way,
    which is why both rows are in the set. Their buffers fill rather than empty, so the
    levels below reach capacity.
    """
    return _seed(
        database,
        "s2",
        Shape(parts=RUNNING_PARTS),
        missing_output=range(STOP_FIRST_MISSING, STOP_LAST_MISSING),
        transitions=[
            *_running("S1", "S2", "S3", "S4"),
            ("S4", STOP_FROM, "Execute", "Suspended", "blocked:outfeed", None),
            (
                "S3",
                STOP_FROM + _seconds(30),
                "Execute",
                "Suspended",
                "blocked:B3_4",
                "B3_4",
            ),
            (
                "S2",
                STOP_FROM + _seconds(60),
                "Execute",
                "Suspended",
                "blocked:B2_3",
                "B2_3",
            ),
            (
                "S1",
                STOP_FROM + _seconds(90),
                "Execute",
                "Suspended",
                "blocked:B1_2",
                "B1_2",
            ),
            *(
                (station, STOP_TO, "Suspended", "Execute", None, None)
                for station in ("S1", "S2", "S3", "S4")
            ),
        ],
        levels=[
            ("B3_4", STOP_FROM - _seconds(60), 5),
            ("B3_4", STOP_FROM + _seconds(25), BUFFER_CAPACITY),
            ("B2_3", STOP_FROM - _seconds(60), 4),
            ("B2_3", STOP_FROM + _seconds(55), BUFFER_CAPACITY),
            ("B1_2", STOP_FROM - _seconds(60), 3),
            ("B1_2", STOP_FROM + _seconds(85), BUFFER_CAPACITY),
        ],
    )


DRIFT_PARTS = 1200
DRIFT_FIRST_MISSING = 700
DRIFT_LAST_MISSING = 750
DRIFT_STOP_FROM = _left_at(DRIFT_FIRST_MISSING - 1)
DRIFT_STOP_TO = _left_at(DRIFT_LAST_MISSING)
DRIFT_FROM_PART = 100
"""Row 3's run is two hours because its chain is not instantaneous: the clamp drifts, the
press reads out of tolerance for `alarm_consecutive_parts`, the alarm is raised and only
then does S2 abort. `_force_alarm_seconds` puts that at 2902 s on the shipped run."""


@pytest.fixture
def scenario_3(database: str) -> Seeded:
    """Row 3: the joining force drifts down at S2, an alarm is raised, and S2 aborts.

    Three consequences and all three are here: `STREAM_FALLS` on `JoiningForcePeak`,
    `ALARM_RAISED` at S2 and `STATION_ABORTS` at S2 — the last two in that order, because an
    alarm is what an abort is raised from and never the other way round.

    **A decoy alarm is raised at the tail before any of it**, unacknowledged and downstream
    of the root. §3.3 and `004_m2c.sql` both warn that "the first station to raise an alarm"
    is circular; here it is also simply wrong, and a chain that consulted alarms would answer
    S4.
    """
    shape = Shape(parts=DRIFT_PARTS, force_drift_from=DRIFT_FROM_PART)
    return _seed(
        database,
        "s3",
        shape,
        missing_output=range(DRIFT_FIRST_MISSING, DRIFT_LAST_MISSING),
        transitions=[
            *_running("S1", "S2", "S3", "S4"),
            ("S2", DRIFT_STOP_FROM - _seconds(60), "Execute", "Aborted", None, None),
            (
                "S3",
                DRIFT_STOP_FROM - _seconds(30),
                "Execute",
                "Suspended",
                "starved:B2_3",
                "B2_3",
            ),
            ("S4", DRIFT_STOP_FROM, "Execute", "Suspended", "starved:B3_4", "B3_4"),
            *(
                (station, DRIFT_STOP_TO, from_state, "Execute", None, None)
                for station, from_state in (
                    ("S2", "Aborted"),
                    ("S3", "Suspended"),
                    ("S4", "Suspended"),
                )
            ),
        ],
        levels=[
            ("B2_3", DRIFT_STOP_FROM - _seconds(120), 4),
            ("B2_3", DRIFT_STOP_FROM - _seconds(35), 0),
            ("B2_3", DRIFT_STOP_FROM - _seconds(32), 0),
            ("B3_4", DRIFT_STOP_FROM - _seconds(120), 5),
            ("B3_4", DRIFT_STOP_FROM - _seconds(5), 0),
            ("B3_4", DRIFT_STOP_FROM - _seconds(2), 0),
            # S1 keeps feeding while S2 is down, so the buffer above S2 fills. The walk must
            # not follow it: S2 is a cause candidate and the chain ends there rather than
            # asking why the buffer above it is full.
            ("B1_2", DRIFT_STOP_FROM - _seconds(120), 6),
            ("B1_2", DRIFT_STOP_FROM - _seconds(10), BUFFER_CAPACITY),
        ],
        alarms=[
            (
                "S4",
                "A-100",
                "outfeed conveyor warning",
                1,
                DRIFT_STOP_FROM - _seconds(150),
                None,
                None,
            ),
            (
                "S2",
                "A-207",
                "joining force out of tolerance",
                2,
                DRIFT_STOP_FROM - _seconds(90),
                DRIFT_STOP_FROM - _seconds(70),
                DRIFT_STOP_TO,
            ),
        ],
    )


# --- §3.5 rows 4 to 8: the quality scenarios --------------------------------------------

CARRIER_WEAR_PARTS = 1100 * CARRIER_COUNT
"""§3.5's own history depth: 33 h at the line's takt is 19,800 parts, 1,100 per carrier, and
that is the depth `test_noise` measured carrier 7 at. Shallower windows are measured in
`measurements/authenticity/README.md` and none of them reaches a verdict."""

CONTAMINATION_PARTS = 6000
FOULING_PARTS = 4000
FOULING_FROM_PART = 1000
LOT_PARTS = 3000


@pytest.fixture
def scenario_4(database: str) -> Seeded:
    """Row 4: `misalignment` and `scratch` rise on carrier 7 and not line-wide."""
    return _seed(
        database, "s4", Shape(parts=CARRIER_WEAR_PARTS, worn_carrier=WORN_CARRIER)
    )


@pytest.fixture
def scenario_5(database: str) -> Seeded:
    """Row 5: feeder lane 2 is contaminated, so `missing_part` and `contamination` rise.

    `CLASS_MIX_SHIFTS`, not `CLASS_RATE_RISES`: the fault has no end, so there is no outside
    window to compare against, and what the run supports is the two named classes against
    the four it does not name.
    """
    return _seed(database, "s5", Shape(parts=CONTAMINATION_PARTS, contaminated=True))


@pytest.fixture
def scenario_6(database: str) -> Seeded:
    """Row 6: the optics foul at S3 — every class's score decays, no verdict moves.

    The scores fall and `Part.rejected` does not read them, which is the whole of
    `SCRAP_RATE_FLAT`: the fouling veils the frame and the classifier loses certainty, and a
    fixture that derived the verdict from the score would have made the two inseparable.
    """
    return _seed(
        database, "s6", Shape(parts=FOULING_PARTS, fouled_from=FOULING_FROM_PART)
    )


@pytest.fixture
def scenario_7(database: str) -> Seeded:
    """Row 7: lane 1 draws a bad lot — `gap` rises while the joining force does not.

    Both consequences are seeded and the second is the one the row exists for: the symptom
    is scenario 3's symptom, and the force is what tells them apart.
    """
    return _seed(
        database,
        "s78",
        Shape(
            parts=LOT_PARTS,
            bad_lot=range(BAD_LOT_INDEX * LOT_SIZE, (BAD_LOT_INDEX + 1) * LOT_SIZE),
        ),
    )


@pytest.fixture
def scenario_8(database: str) -> Seeded:
    """Row 8: one defective component instead of five hundred.

    Seeded under the same name as row 7, so the two runs draw identically about every part
    and differ only in what was injected — which is what makes the difference between their
    answers attributable to the injection rather than to the seed.
    """
    return _seed(
        database,
        "s78",
        Shape(parts=LOT_PARTS, defective_part=DEFECTIVE_COMPONENT_PART),
    )


# --- reading the answers ----------------------------------------------------------------


def _body(client: TestClient, path: str, params: dict[str, str]) -> dict[str, object]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _rows(body: dict[str, object], key: str) -> list[dict[str, object]]:
    rows = body[key]
    assert isinstance(rows, list)
    return [dict(row) for row in rows]


def _dimension(body: dict[str, object], name: str) -> dict[str, object]:
    matching = [row for row in _rows(body, "dimensions") if row["dimension"] == name]
    assert len(matching) == 1, f"expected one {name} dimension"
    return matching[0]


def _significant(body: dict[str, object], name: str) -> list[dict[str, object]]:
    section = _dimension(body, name)
    return [
        value
        for value in _rows(section, "patterns")
        if value["verdict"] == "significant"
    ]


def _the_one_stop(client: TestClient, seeded: Seeded) -> dict[str, object]:
    """The single stop in the window, as `/stops` reports it."""
    body = _body(client, "/stops", seeded.window)
    stops = _rows(body, "stops")
    assert len(stops) == 1, f"expected one stop, got {stops}"
    return stops[0]


def _chain(client: TestClient, stop: dict[str, object]) -> dict[str, object]:
    """One stop's derivation: the links, where it terminated, and what it terminated at."""
    identifier = stop["id"]
    assert isinstance(identifier, str)
    detail = _body(client, f"/stops/{identifier}", {})
    derivation = detail["derivation"]
    assert isinstance(derivation, dict)
    return dict(derivation)


def _stream_shift(client: TestClient, seeded: Seeded) -> float:
    """How far S2's clamp peak moved across the window, in units of its own spread.

    `test_scenario_consequences`' own form for the same claim, so the two numbers compare:
    it measures scenario 3's force falling 10.7 σ of its 39.4 N part-to-part spread and
    scenario 7's moving 0.059. The spread is taken from the head of the window because a
    drift inflates the spread of the whole of it — measured over a drifted run the total
    standard deviation is the drift's own, and the ratio would understate a real fall.
    """
    body = _body(
        client,
        "/signals/trend",
        {
            **seeded.window,
            "station": "S2",
            "signal": JOINING_FORCE_SIGNAL,
            "agg": "raw",
        },
    )
    values = [point["value"] for point in _rows(body, "points")]
    assert all(isinstance(value, float) for value in values)
    numbers = [float(value) for value in values if isinstance(value, float)]
    assert len(numbers) == len(values) > 100
    tenth = len(numbers) // 10
    head, tail = numbers[:tenth], numbers[-tenth:]
    return (statistics.fmean(head) - statistics.fmean(tail)) / statistics.stdev(head)


# --- rows 1 to 3: the chain, computed ---------------------------------------------------


def test_scenario_1_resolves_to_the_head_of_the_line_and_outside_it(
    scenario_1: Seeded, client: TestClient
) -> None:
    """§3.5 row 1 → `external_upstream`, rooted at S1.

    Every link is asserted, not only the category: §5.4 returns a derivation so that it can
    be contradicted, and a category with the chain unchecked is the same answer with the
    checking removed. The chain has to walk S4 → B3_4 → S3 → B2_3 → S2 → B1_2 → S1 and stop
    there because `starved:feeder` names nothing this line contains.
    """
    stop = _the_one_stop(client, scenario_1)
    assert stop["category"] == "external_upstream"

    derivation = _chain(client, stop)
    links = _rows(derivation, "links")
    assert [(link["station"], link["buffer"]) for link in links] == [
        ("S4", "B3_4"),
        ("S3", "B2_3"),
        ("S2", "B1_2"),
        ("S1", None),
    ]
    assert links[-1]["reason"] == "starved:feeder"
    assert derivation["termination"] == "line_edge"
    assert derivation["cause_candidates"] == []


def test_scenario_2_resolves_to_the_tail_of_the_line_and_outside_it(
    scenario_2: Seeded, client: TestClient
) -> None:
    """§3.5 row 2 → `external_downstream`, rooted at S4.

    One link, and that is the answer rather than a short chain: the stop is an absence of
    output at the tail, and the tail's own reason already names something outside the line.
    The three stations backing up behind it are consequences of the same blockage and the
    walk is right not to climb them.
    """
    stop = _the_one_stop(client, scenario_2)
    assert stop["category"] == "external_downstream"

    derivation = _chain(client, stop)
    links = _rows(derivation, "links")
    assert [link["station"] for link in links] == ["S4"]
    assert links[0]["reason"] == "blocked:outfeed"
    assert derivation["termination"] == "line_edge"


def test_scenario_3_resolves_to_an_internal_cause_at_s2(
    scenario_3: Seeded, client: TestClient
) -> None:
    """§3.5 row 3 → `internal`, rooted at S2, and found without reading an alarm.

    The decoy alarm at S4 is raised sixty seconds before S2's own and is still standing when
    the chain is drawn, so it is in the response beside the derivation — and the root is S2
    anyway. That is the circularity guard §3.3 asks for, made concrete.
    """
    stop = _the_one_stop(client, scenario_3)
    assert stop["category"] == "internal"

    detail_alarms = _rows(
        _body(client, f"/stops/{stop['id']}", {}),
        "alarms",
    )
    assert {alarm["code"] for alarm in detail_alarms} == {"A-100", "A-207"}

    derivation = _chain(client, stop)
    links = _rows(derivation, "links")
    assert [(link["station"], link["buffer"]) for link in links] == [
        ("S4", "B3_4"),
        ("S3", "B2_3"),
        ("S2", None),
    ]
    assert links[-1]["state"] == "Aborted"
    assert derivation["termination"] == "cause_candidate"
    assert [
        candidate["station"] for candidate in _rows(derivation, "cause_candidates")
    ] == ["S2"]


def test_scenario_3_shows_the_joining_force_falling(
    scenario_3: Seeded, client: TestClient
) -> None:
    """Row 3's first consequence, read back off the stream §3.5 names.

    The bar is deliberately far below the drift's own size: what this has to separate is a
    clamp that moved from one that did not, and row 7 is the other side of it.
    """
    assert _stream_shift(client, scenario_3) > 5.0


# --- rows 4 to 6: quality, and the answers that are not "look here" ---------------------


def _assert_no_stop(client: TestClient, seeded: Seeded) -> None:
    """The line never stopped, and the analysis says so.

    Asserted for every quality scenario because it is the answer most easily got wrong in
    the safe-looking direction: three of §3.5's rows change what the line *makes* and
    nothing about whether it is running, and a stop invented for one of them would be a
    diagnosis of an event that did not happen.
    """
    body = _body(client, "/stops", seeded.window)
    assert _rows(body, "stops") == []
    assert body["micro_stops"] == 0


def test_scenario_4_finds_carrier_7_only_at_the_plants_own_history_depth(
    scenario_4: Seeded, client: TestClient
) -> None:
    """§3.5 row 4: the worn carrier, and the pool's own worst carrier beside it.

    Two findings rather than one, and the second is not a defect: carrier 18 is the top of
    §3.5's own quality spread and genuinely scraps more than the rest, so reporting it is
    what §5.5's correction promises — Benjamini-Hochberg bounds the share of the findings
    that are false, and this one is not false. What would be a defect is carrier 7 missing,
    and what would be a different defect is a report of half the pool.
    """
    _assert_no_stop(client, scenario_4)

    body = _body(client, "/inspection/patterns", scenario_4.window)
    found = _significant(body, "carrier")
    assert str(WORN_CARRIER) in {value["value"] for value in found}
    assert len(found) <= 2, f"the noise floor is being reported as findings: {found}"

    worn = next(value for value in found if value["value"] == str(WORN_CARRIER))
    observed, expected = worn["observed_share"], worn["expected_share"]
    assert isinstance(observed, float) and isinstance(expected, float)
    assert observed > expected


def test_scenario_5_shifts_the_class_mix_and_refuses_to_name_the_lane(
    scenario_5: Seeded, client: TestClient
) -> None:
    """§3.5 row 5: the two named classes rise, and `lane` carries no verdict at all.

    The refusal is the half that matters. §3.5 says this line cannot distinguish lanes —
    every assembly draws one component from each, so there is no contrast group — and
    "not significant" over two groups holding the same parts would be a true-sounding
    sentence about a comparison that was never made.

    The four classes the fault does not name come out significantly *below* the pool, which
    is arithmetic rather than a second finding: raising two of six classes raises the pool
    every class is compared against. So the claim is about the classes above expectation.
    """
    _assert_no_stop(client, scenario_5)

    body = _body(client, "/inspection/patterns", scenario_5.window)

    lane = _dimension(body, "lane")
    assert lane["comparable"] is False
    assert lane["patterns"] == []
    reason = lane["not_comparable"]
    assert isinstance(reason, str) and "contrast group" in reason

    raised = {
        value["value"]
        for value in _significant(body, "defect_class")
        if isinstance(value["effect_size"], float) and value["effect_size"] > 0
    }
    assert raised == set(CONTAMINATION_CLASSES)


def test_scenario_6_decays_every_score_while_the_scrap_rate_holds(
    scenario_6: Seeded, client: TestClient
) -> None:
    """§3.5 row 6: the breakdown empties, the reject count does not, and nothing is a pattern.

    The decay reaches the analysis as `rejects_without_class` — §3.4's six scores are all
    still there and all of them have fallen under the threshold a class is counted at.
    Without that number the same window reads as "no defects seen", which
    `models.InspectionStats` calls the quietest wrong answer this endpoint can give.

    And the fouling is correctly *not* a pattern: no class stands out, because they fell
    together, and no time bucket does, because no verdict moved.
    """
    _assert_no_stop(client, scenario_6)

    clean = _body(
        client,
        "/inspection/stats",
        {
            "from": PROOF_START.isoformat(),
            "to": _inspected_at(FOULING_FROM_PART).isoformat(),
        },
    )
    fouled = _body(
        client,
        "/inspection/stats",
        {
            "from": _inspected_at(2 * FOULING_FROM_PART).isoformat(),
            "to": scenario_6.window["to"],
        },
    )

    clean_rejects, fouled_rejects = clean["rejects"], fouled["rejects"]
    clean_total, fouled_total = clean["total"], fouled["total"]
    assert isinstance(clean_rejects, int) and isinstance(fouled_rejects, int)
    assert isinstance(clean_total, int) and isinstance(fouled_total, int)
    assert clean_rejects and fouled_rejects
    # The scrap rate is the same to within a fifth of itself over a run this length; what
    # the classifier reports about those same rejects is not.
    assert fouled_rejects / fouled_total == pytest.approx(
        clean_rejects / clean_total, rel=0.2
    )

    assert clean["rejects_without_class"] == 0
    unexplained = fouled["rejects_without_class"]
    assert isinstance(unexplained, int)
    assert unexplained > 0.8 * fouled_rejects

    body = _body(client, "/inspection/patterns", scenario_6.window)
    assert _significant(body, "defect_class") == []
    assert _significant(body, "time_bucket") == []
    assert _significant(body, "lot") == []


# --- rows 7 and 8: the lot, and the one part that is not a lot ---------------------------


def test_scenario_7_names_the_lot_and_not_a_drifting_press(
    scenario_7: Seeded, client: TestClient
) -> None:
    """§3.5 row 7, the row this milestone is most at risk of getting wrong.

    Rising `gap` is scenario 3's symptom, and DP-01 attributes it to a drifting joining
    process at S2. Here that is the wrong answer, and the two things this asserts are the
    two halves of not giving it: the force did not move, and the defects follow the supplier
    lot.

    **M3 stops short of calling the lot the cause** — that is interpretation and M4's. What
    it must do is decline the mechanical answer and surface the evidence that separates
    them, which is what a significant lot beside a flat stream is.

    The lot beats the clock as well, and that is the sharper half: the bad lot straddles an
    hourly bucket boundary, so no time bucket reaches a verdict while the lot does. A read
    path that reconstructed lot membership by joining on time could not produce that.
    """
    _assert_no_stop(client, scenario_7)
    assert _body(client, "/alarms", scenario_7.window)["alarms"] == []

    assert abs(_stream_shift(client, scenario_7)) < 1.0

    body = _body(client, "/inspection/patterns", scenario_7.window)
    raised = {
        value["value"]
        for value in _significant(body, "defect_class")
        if isinstance(value["effect_size"], float) and value["effect_size"] > 0
    }
    assert raised == {GAP}

    lots = _significant(body, "lot")
    assert [value["value"] for value in lots] == [
        f"L-{BAD_LOT_LANE}-{BAD_LOT_INDEX:02d}"
    ]
    assert _significant(body, "time_bucket") == []
    assert _significant(body, "carrier") == []


def test_scenario_8_does_not_turn_one_bad_part_into_a_bad_lot(
    scenario_8: Seeded, client: TestClient
) -> None:
    """§3.5 row 8: row 7's mirror, and the judgement §3.5 calls routine and consequential.

    The same fault on one component instead of five hundred must read as one bad part. The
    lot dimension is where that distinction is made or lost, and here it reports nothing —
    not for want of data, which would be a different answer: every lot clears the sample
    gate and carries a verdict, and the verdict is that nothing separates it from the pool.
    """
    _assert_no_stop(client, scenario_8)

    body = _body(client, "/inspection/patterns", scenario_8.window)
    assert _significant(body, "lot") == []
    assert _significant(body, "defect_class") == []
    assert _significant(body, "time_bucket") == []

    lot_values = _rows(_dimension(body, "lot"), "patterns")
    assert lot_values
    assert all(value["verdict"] == "not_significant" for value in lot_values)


def test_the_defective_component_is_still_findable_as_a_part(
    scenario_8: Seeded, client: TestClient
) -> None:
    """Nothing to diagnose is not nothing to do.

    Row 8's part carries `gap` and `/parts/affected` returns it with the handful the noise
    floor produced — which is the containment answer a plant acts on when the statistics
    correctly say there is no pattern. The two are different questions and this is the one
    that still has an answer.
    """
    serial = f"A-{DEFECTIVE_COMPONENT_PART:08d}"
    body = _body(
        client,
        "/parts/affected",
        {**scenario_8.window, "defect_class": GAP},
    )
    parts = body["parts"]
    assert isinstance(parts, dict)
    found = {
        serial_of
        for group in ("rejected", "shipped", "on_the_line")
        for serial_of in _serials(parts, group)
    }
    assert serial in found


def _serials(parts: dict[str, object], group: str) -> list[str]:
    section = parts[group]
    assert isinstance(section, dict)
    serials = section["serials"]
    assert isinstance(serials, list)
    return [str(entry) for entry in serials]
