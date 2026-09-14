"""Fixtures for the analysis tests.

Real Postgres in a container rather than a fake: the endpoints are almost entirely SQL, and
a fake would assert that the queries are the ones written rather than that they are right.
The schema comes from the gateway's migrations so there is one definition of it in the
repository, not a second one drifting quietly here.

**The seeded rows are the widened shape, not M1's.** Until M2b Task 7 this file applied
`001_m1.sql` alone and seeded `inspection_results.defect_class`, a scalar the plant stopped
sending when §3.4's verdict became a vector — so `/inspection/stats` was answering
`by_defect_class: []` against a live database while every test here passed against a schema
three migrations behind. A fixture that seeds a shape the plant cannot produce is a green
test pinning a fiction, and this one was hiding the endpoint it was meant to cover.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from analysis.app import app
from analysis.config import Settings
from analysis.db import reset_pool
from analysis.dependencies import settings_dependency
from fastapi.testclient import TestClient
from psycopg import Connection, sql
from testcontainers.postgres import PostgresContainer

MIGRATIONS = Path(__file__).resolve().parents[2] / "gateway" / "Gateway" / "Migrations"
"""Every migration the gateway embeds, applied in the order their names give.

Read from the directory rather than listed, for the reason `PostgresWriter.Migrations()`
reads its own embedded resources rather than restating them: a migration added and not
listed here would leave these tests passing against a schema the service does not have.
"""

# Pinned by digest, not tag (§10.7). scripts/pin-images.sh re-resolves it.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

WINDOW_START = datetime(2026, 9, 12, 1, 0, 0, tzinfo=UTC)
TAKT = timedelta(seconds=6)
PARTS_IN_AN_HOUR = 600
# Every 20th part, which is a density chosen for the fixture and not the plant's rate:
# D10 puts that at §3.5's 1.5 %, nine rejects in this window, too few to spread over six
# defect classes and still assert a breakdown. Deliberately not tracking
# `Settings.reject_rate` -- these are query tests over seeded rows, and a fixture that
# moved whenever the plant was retuned would make them fail for a reason that is not theirs.
REJECT_EVERY = 20
REJECT_OFFSET = 7  # so A-00000007 is a reject and A-00000006 is not

BEFORE_THE_WINDOW = WINDOW_START - timedelta(hours=1)
"""Where the rows that exist to be *looked up* rather than counted are placed.

The half-open window tests count what lies in `[WINDOW_START, WINDOW_START + 1h)` exactly,
so a part seeded to exercise a null in the trace has to sit outside it or it moves a total
that is asserted to the unit."""

TWIN_INSTANT = WINDOW_START + timedelta(minutes=30)
"""Two assemblies are created here, at the same instant, on different carriers.

This is the fixture's sharpest edge: at `TWIN_INSTANT` the question "which part was at S2
just now" has two correct answers, so anything that reconstructs a part's press record from
a time range gets one of them wrong. In the real line the same ambiguity arrives through
buffers and jitter rather than through an exact tie; the tie is how a unit test states it.
"""

DEFECT_CLASSES = (
    "gap",
    "crack",
    "misalignment",
    "missing_part",
    "scratch",
    "contamination",
)
"""`inspection.classifier.DEFECT_CLASSES`, in the classifier's own order.

`defect_classes` and `confidences` ride every inspection event as parallel arrays over all
six -- a good part is six low scores, not an absent vector -- so this is the shape the
endpoint has to read, and seeding anything narrower would test a plant that does not exist.
"""

BASELINE_SCORE = 0.03
"""What every class scores on a part the model believes is clean.

`inspection.classifier` draws the baseline from [0.01, 0.08) and boosts a class it believes
it saw into [0.55, 0.95). A single value stands in for the baseline here because these are
query tests: what matters is that it is far below anything a boosted class reaches.
"""
BOOSTED_SCORE = 0.82

CURVE_SAMPLES = 51
"""`simulator.config.curve_samples`. The length is asserted rather than the shape: what
the trace has to carry is the curve the press recorded, and a reconstruction from the time
series cannot produce one at all."""

DECOY_FORCE = 900.0
"""S2's historised `JoiningForcePeak` in this fixture. The decoy spans [900, 909] and every
per-part `PeakForce` spans [100.0, 100.599], so the two ranges do not overlap and the
nearest pair of values is a factor of ~9 apart.

Disjointness is the property the proof rests on, not the size of the gap -- a reconstructed
answer has to be unmistakable, and any separation wider than the per-part spread gives that.
The time series is seeded densely across the window *on purpose*: §3.4a's reconstruction is
tempting precisely because the data is there, so a fixture with nothing to reconstruct from
would let a read path that joined on time pass by finding nothing. Every trace assertion in
these tests would return this number instead of the part's own if the endpoint ever did."""
DECOY_DISTANCE = 50.0
"""The same for `JoiningDistance`: the decoy spans [50, 59] against a per-part
[10.0, 10.0599], so ~5x apart at the nearest and, again, non-overlapping."""


def peak_force(index: int) -> float:
    """The press record for part `index`, in a range no decoy row reaches."""
    return round(100.0 + index * 0.001, 6)


def joining_distance(index: int) -> float:
    return round(10.0 + index * 0.0001, 6)


def curve_for(index: int) -> list[float]:
    return [round(0.5 * sample + index * 0.001, 6) for sample in range(CURVE_SAMPLES)]


def _defect_index(index: int) -> int:
    """Which class the model believes it saw on the reject at `index`.

    Cycles through all six so the breakdown has something to break down: the 30 rejects in
    the window come out five to a class, which is a count a wrong query cannot land on.
    """
    return (index // REJECT_EVERY) % len(DEFECT_CLASSES)


def _scores(index: int, reject: bool) -> list[float]:
    """The six per-class scores for part `index`, in `DEFECT_CLASSES` order."""
    scores = [BASELINE_SCORE] * len(DEFECT_CLASSES)
    if not reject:
        return scores
    scores[_defect_index(index)] = BOOSTED_SCORE
    if index == REJECT_OFFSET:
        # One part the model believes carries two defects at once. §3.4 requires exactly
        # this -- the six scores are independent and do not sum to 1 -- and §3.5's
        # scenarios 4 and 5 turn on it. Without such a part in the fixture, counting the
        # classes that scored high and counting each reject's single strongest class give
        # the same answer, and a breakdown that quietly collapses to one class per part
        # would pass.
        scores[DEFECT_CLASSES.index("scratch")] = BOOSTED_SCORE
    return scores


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        with psycopg.connect(container.get_connection_url()) as conn:
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                conn.execute(migration.read_text())
            conn.commit()
        yield container


@pytest.fixture(scope="session")
def postgres(postgres_container: PostgresContainer) -> str:
    # str(), because testcontainers ships no py.typed and mypy cannot take its word for
    # the return type -- see the [mypy-testcontainers.*] override.
    return str(postgres_container.get_connection_url())


@pytest.fixture
def database(postgres: str) -> Iterator[str]:
    with psycopg.connect(postgres) as conn:
        # Read from the catalogue rather than listed, for the same reason MIGRATIONS is:
        # a table added by a migration and forgotten here would carry one test's rows into
        # the next, and the failure would land on whichever test ran second.
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        ]
        conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(table) for table in tables)
            )
        )
        conn.execute(
            "INSERT INTO stations (code, name, position_in_line) VALUES"
            " ('S1', 'Feeding', 1), ('S2', 'Joining', 2),"
            " ('S3', 'Inspection', 3), ('S4', 'Outfeed', 4)"
        )
        conn.commit()
    reset_pool()
    yield postgres
    reset_pool()


def _station_ids(conn: Connection) -> dict[str, int]:
    return {code: id_ for code, id_ in conn.execute("SELECT code, id FROM stations")}


def _seed_lots(conn: Connection) -> dict[str, int]:
    """Two feeder lanes, and a lane that changes lot halfway through the window.

    The change is what makes "which lot was this component drawn from" a question with a
    wrong answer available: a part from either half resolves to the same lane.
    """
    lots: dict[str, int] = {}
    for lot_code, lane, supplier, loaded_at in (
        ("LOT-A1", 1, "Acme", BEFORE_THE_WINDOW),
        ("LOT-A2", 1, "Acme", WINDOW_START + timedelta(minutes=30)),
        ("LOT-B1", 2, "Borealis", BEFORE_THE_WINDOW),
    ):
        row = conn.execute(
            "INSERT INTO component_lots (lot_code, lane, supplier, loaded_at)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (lot_code, lane, supplier, loaded_at),
        ).fetchone()
        assert row is not None
        lots[lot_code] = row[0]
    return lots


def _lot_for(index: int) -> tuple[str, str]:
    """The lot each lane was drawing from when part `index` was created."""
    return ("LOT-A1" if index < PARTS_IN_AN_HOUR // 2 else "LOT-A2", "LOT-B1")


def _insert_assembly(
    conn: Connection,
    serial: str,
    created_at: datetime | None,
    carrier_id: int | None,
) -> None:
    if carrier_id is not None:
        conn.execute(
            "INSERT INTO carriers (id) VALUES (%s) ON CONFLICT DO NOTHING",
            (carrier_id,),
        )
    conn.execute(
        "INSERT INTO assemblies (serial, created_at, carrier_id) VALUES (%s, %s, %s)",
        (serial, created_at, carrier_id),
    )


def _insert_component(
    conn: Connection,
    serial: str,
    lot_id: int | None,
    lane: int | None,
    read_at: datetime | None,
) -> None:
    conn.execute(
        "INSERT INTO components (serial, lot_id, lane, read_at) VALUES (%s, %s, %s, %s)",
        (serial, lot_id, lane, read_at),
    )


def _insert_genealogy(
    conn: Connection, assembly: str, component: str, position: int
) -> None:
    conn.execute(
        "INSERT INTO genealogy (assembly_serial, component_serial, position)"
        " VALUES (%s, %s, %s)",
        (assembly, component, position),
    )


def _insert_press_record(
    conn: Connection, serial: str, station_id: int, index: int
) -> None:
    """§3.4a's per-part record: two scalars and the curve, against the serial.

    No timestamp column on either table, which is the point -- the row is reachable by
    serial and by nothing else.
    """
    for signal, value in (
        ("PeakForce", peak_force(index)),
        ("JoiningDistance", joining_distance(index)),
    ):
        conn.execute(
            "INSERT INTO part_process_values (assembly_serial, station_id, signal, value)"
            " VALUES (%s, %s, %s, %s)",
            (serial, station_id, signal, value),
        )
    conn.execute(
        "INSERT INTO part_process_curves (assembly_serial, station_id, signal, samples)"
        " VALUES (%s, %s, 'Curve', %s)",
        (serial, station_id, curve_for(index)),
    )


def _insert_inspection(
    conn: Connection,
    serial: str,
    at: datetime,
    station_id: int,
    index: int,
    reject: bool,
    carrier_id: int | None,
) -> None:
    conn.execute(
        "INSERT INTO inspection_results (assembly_serial, source_ts, station_id, result,"
        " confidence, model_version, image_ref, carrier_id, defect_classes, confidences)"
        " VALUES (%s, %s, %s, %s, %s, 'sim-1', %s, %s, %s, %s)",
        (
            serial,
            at,
            station_id,
            "reject" if reject else "good",
            0.87 if reject else 0.97,
            serial if reject else None,
            carrier_id,
            list(DEFECT_CLASSES),
            _scores(index, reject),
        ),
    )
    if reject:
        conn.execute(
            "INSERT INTO inspection_images (assembly_serial, bytes) VALUES (%s, %s)",
            (serial, b"\x89PNG\r\n\x1a\n" + b"x" * 64),
        )


def _insert_disposition(
    conn: Connection, serial: str, at: datetime, index: int, reject: bool
) -> None:
    conn.execute(
        "INSERT INTO part_dispositions (assembly_serial, at, disposition, reason)"
        " VALUES (%s, %s, %s, %s)",
        (
            serial,
            at,
            "reject" if reject else "good",
            DEFECT_CLASSES[_defect_index(index)] if reject else None,
        ),
    )


def _seed_decoy_time_series(conn: Connection, station_id: int) -> None:
    """S2's historised streams, across the whole window, at values no part carries.

    §4.1 publishes these as `JoiningForcePeak` and `JoiningDistance`; the per-part record
    names the same two numbers `PeakForce` and `JoiningDistance`, because the gateway
    translates no vocabularies. Both names are here so that a read path reaching for the
    time series finds something to reach for under either.
    """
    for index in range(PARTS_IN_AN_HOUR):
        at = WINDOW_START + TAKT * index
        for signal, value in (
            ("JoiningForcePeak", DECOY_FORCE + index % 10),
            ("PeakForce", DECOY_FORCE + index % 10),
            ("JoiningDistance", DECOY_DISTANCE + index % 10),
        ):
            conn.execute(
                "INSERT INTO signals (station_id, signal, source_ts, value)"
                " VALUES (%s, %s, %s, %s)",
                (station_id, signal, at, value),
            )


def _seed_parts(url: str) -> None:
    with psycopg.connect(url) as conn:
        stations = _station_ids(conn)
        lots = _seed_lots(conn)
        _seed_decoy_time_series(conn, stations["S2"])

        for index in range(PARTS_IN_AN_HOUR):
            serial = f"A-{index:08d}"
            reject = index % REJECT_EVERY == REJECT_OFFSET
            inspected_at = WINDOW_START + TAKT * index
            # S1 creates the assembly two stations before S3 inspects it. The inspection
            # instant is what the window tests count, so it is the one held fixed.
            created_at = inspected_at - 2 * TAKT
            carrier_id = index % 15 + 1

            _insert_assembly(conn, serial, created_at, carrier_id)
            for position, (lane, lot_code) in enumerate(
                zip((1, 2), _lot_for(index), strict=True)
            ):
                # `simulator.identity.component_serial`'s spelling, separators included.
                # The lane is a field of the serial there so a containment query can read
                # it back off one, and a fixture that ran the two together would pin a
                # prefix no lane query can split.
                component = f"C-{lane}-{index:08d}"
                _insert_component(conn, component, lots[lot_code], lane, created_at)
                _insert_genealogy(conn, serial, component, position)

            _insert_press_record(conn, serial, stations["S2"], index)
            _insert_inspection(
                conn, serial, inspected_at, stations["S3"], index, reject, carrier_id
            )
            _insert_disposition(conn, serial, inspected_at + TAKT, index, reject)

        _seed_twins(conn, stations)
        _seed_stubs(conn, stations, lots)
        conn.commit()


def _seed_twins(conn: Connection, stations: dict[str, int]) -> None:
    """Two assemblies created at one instant, pressed, and not yet inspected.

    Both halves are deliberate. The shared instant is what a time-range reconstruction
    cannot resolve; the missing verdict is the ordinary state of every part between S2 and
    S3 at the moment a backfill's upper bound falls, and §14's trace has to answer for one
    rather than omitting it.
    """
    for twin, carrier in (("A-TWIN-1", 3), ("A-TWIN-2", 4)):
        _insert_assembly(conn, twin, TWIN_INSTANT, carrier)
        for position, lane in enumerate((1, 2)):
            component = f"C-{lane}-{twin}"
            _insert_component(conn, component, None, lane, TWIN_INSTANT)
            _insert_genealogy(conn, twin, component, position)

    _insert_press_record(conn, "A-TWIN-1", stations["S2"], 1)
    _insert_press_record(conn, "A-TWIN-2", stations["S2"], 2)


def _seed_stubs(
    conn: Connection, stations: dict[str, int], lots: dict[str, int]
) -> None:
    """The two shapes the gateway's history horizon leaves behind, both real.

    `A-HORIZON` is an assembly whose `AssemblyCreatedEvent` lies before the horizon: the
    row exists because a later station named the serial, and it carries neither a creation
    instant nor a carrier nor any genealogy, because the one event that would have carried
    all three never arrived.

    `A-STUBLOT` is the other direction. Its creation event did arrive and named two
    components, but one of those components was drawn before the horizon, so its own read
    event never came and the row says what is true: this component exists, and nothing
    else about it is known.
    """
    _insert_assembly(conn, "A-HORIZON", None, None)
    _insert_press_record(conn, "A-HORIZON", stations["S2"], 3)
    _insert_inspection(
        conn, "A-HORIZON", BEFORE_THE_WINDOW, stations["S3"], 3, False, None
    )
    _insert_disposition(conn, "A-HORIZON", BEFORE_THE_WINDOW + TAKT, 3, False)

    _insert_assembly(conn, "A-STUBLOT", BEFORE_THE_WINDOW, 1)
    _insert_component(conn, "C-1-STUB", None, None, None)
    _insert_genealogy(conn, "A-STUBLOT", "C-1-STUB", 0)
    _insert_component(conn, "C-2-STUBLOT", lots["LOT-B1"], 2, BEFORE_THE_WINDOW)
    _insert_genealogy(conn, "A-STUBLOT", "C-2-STUBLOT", 1)


@pytest.fixture
def seeded_db(database: str) -> str:
    _seed_parts(database)
    return database


DECAYED_SCORE = 0.40
"""Every class on one reject, all of them below the threshold.

§3.5 scenario 6 -- "confidence decays across all classes while the scrap rate stays flat"
-- is a run of these, and it is the shape that makes an empty breakdown a measurement
rather than a bug. Above `BASELINE_SCORE`, because the scores have not vanished; below
`Settings.defect_class_threshold`, because none of them is a call any more.
"""


@pytest.fixture
def seeded_db_with_unaccounted_rejects(database: str) -> str:
    """The two shapes that leave a reject out of `by_defect_class`, both real.

    `A-LEGACY` is a row written before §3.4's vector existed: `defect_classes` and
    `confidences` are NULL, which `models.Inspection` documents as a row predating the
    widened event rather than a part that scored nothing. `A-DECAYED` is scenario 6 --
    every class present, every one of them fallen below the threshold.

    Both sit inside the counted window on purpose: the point of the fixture is that
    `rejects` moves and the breakdown does not.
    """
    _seed_parts(database)
    with psycopg.connect(database) as conn:
        stations = _station_ids(conn)
        for serial, classes, scores in (
            ("A-LEGACY", None, None),
            (
                "A-DECAYED",
                list(DEFECT_CLASSES),
                [DECAYED_SCORE] * len(DEFECT_CLASSES),
            ),
        ):
            _insert_assembly(conn, serial, WINDOW_START, 1)
            conn.execute(
                "INSERT INTO inspection_results (assembly_serial, source_ts, station_id,"
                " result, confidence, model_version, defect_classes, confidences)"
                " VALUES (%s, %s, %s, 'reject', 0.87, 'sim-1', %s, %s)",
                (serial, WINDOW_START, stations["S3"], classes, scores),
            )
        conn.commit()
    return database


@pytest.fixture
def seeded_db_with_gap(database: str) -> str:
    _seed_parts(database)
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO ingest_gaps (from_ts, to_ts, reason) VALUES (%s, %s, 'plant_unreachable')",
            (
                WINDOW_START + timedelta(minutes=20),
                WINDOW_START + timedelta(minutes=25),
            ),
        )
        conn.commit()
    return database


@pytest.fixture
def client(database: str) -> Iterator[TestClient]:
    app.dependency_overrides[settings_dependency] = lambda: Settings(
        database_url=database
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
