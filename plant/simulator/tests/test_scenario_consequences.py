"""What M2c proves: §3.5's eight scenarios, and the twelve consequences they claim between
them, read back out of §5.2's tables.

**Nothing here diagnoses anything, and that is the line this file exists not to cross.**
Every assertion below is of the form "this is in the history": a chain of suspensions in
order, a class rate that separates from its own baseline, a stream that did not move. No
assertion says what an analysis should conclude, none names a cause, and none reads the
ground-truth log's *per-part* truth -- M3 infers a cause and M7 scores the inference, and
a proof written here that anticipated either would be the answer key written by the
reasoning it is later used to grade.

**Every test has the same three steps.** A scenario's own `Injection` is written to the
ground-truth log with the consequences §3.5's row claims for it; the line runs; the run's
published output is loaded into a real PostgreSQL carrying the gateway's own migrations.
The consequences are then read **back out of the log file** -- not off the `Scenario`
object -- and each is dispatched to a checker that asserts it with SQL. Reading the log
back is what makes this a proof about the artefact a later milestone is handed, rather
than about the objects this process happens to hold.

**Consequences are asserted for `source == "scenario"` only.** §3.7's panel writes an
injection with `"consequences": []` by construction, because nobody wrote down what should
follow from a fault chosen at a keyboard. An empty list there is the honest record and not
a scenario that claimed nothing, so the dispatcher selects on `source` --
`test_only_a_scenarios_injection_carries_a_claim_to_assert` is what stops that selection
being decoration.

**What this proves, and what it does not.** The rows here are the plant's own published
output materialised into §5.2's schema by `_load` below; they did not travel over OPC UA
and they were not written by the gateway. So this proves that the *plant* produces the
consequences §3.5 claims, in the shape the analysis will read them in -- and it proves
nothing about transport. §1.2 and §1.3 own that half, behind `make verify`, and saying so
here is cheaper than a reader assuming this file covers it.
"""

from __future__ import annotations

import base64
import itertools
import json
import math
import statistics
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from conftest import (
    ProduceFactory,
    Run,
    consequence_claiming,
    fault_window,
    new_clock,
    paired_runs,
    rms_contrast,
    scenario_run,
    truth_outcome,
)
from psycopg import Connection, sql
from simulator.address_space import BUFFERS, STATION_SIGNALS
from simulator.config import Settings
from simulator.events import ALARM, INSPECTION_RESULT, EventType
from simulator.faults import FaultKind
from simulator.ground_truth import GroundTruthLog, Injector, open_log, recording
from simulator.inspection_client import DEFECT_CLASSES, InspectionClient
from simulator.packml import State
from simulator.render import render_part
from simulator.scenarios import (
    ALARM_RAISED,
    BLOCKED_IN_ORDER,
    CLASS_CONCENTRATES,
    CLASS_MIX_SHIFTS,
    CLASS_RATE_RISES,
    CONFIDENCE_DECAYS,
    ONE_PART_ONLY,
    SCRAP_RATE_FLAT,
    STATION_ABORTS,
    STREAM_FALLS,
    STREAM_STABLE,
    SUSPENDED_IN_ORDER,
    Scenario,
    all_scenarios,
    scenario,
)
from simulator.stations.base import PartOutcome, ProduceFn
from testcontainers.postgres import PostgresContainer

MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "diagnostics"
    / "gateway"
    / "Gateway"
    / "Migrations"
)
"""Every migration the gateway embeds, applied in the order their names give.

Read from the directory rather than listed, and from the gateway's own copy rather than
transcribed: §5.2's shape is defined once in this repository, and a hand-written table
here would be the fixture-three-migrations-behind defect that
`diagnostics/analysis/tests/conftest.py` records paying for -- rows in a shape the plant
cannot produce, pinned green.

This is the plant workspace reading a file from the diagnostics one at test time. No
container joins a network it may not and nothing in the plant stack gains a dependency;
the invariant the two workspaces exist for is about what runs, and is untouched.
"""

# Pinned by digest, not tag (§10.7), and the same digest the analysis suite already
# starts -- one opinion in this repository about which PostgreSQL it is tested against.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

OWNERSHIP_MIGRATIONS = frozenset({"005_m3_read_layer.sql"})
"""The migrations this fixture applies to neither schema.

Every other file under `MIGRATIONS` defines §5.2's tables, which is what this fixture wants
two copies of. 005 defines who *owns* them: it moves the tables into `ingest`, builds the
analysis service's `read` views over that one schema, creates a database role and sets a
database-wide search path. Those are properties of the *database* rather than of a schema,
so there is no such thing as two copies of them -- and there is no gateway here, no analysis
service, and nothing to grant to. The two schemas below are this file's own scaffolding for
a contrast group, not a deployment.

A set rather than one name, because that is the shape the rule has: a migration belongs here
when it names a schema, a role or a database setting that only one deployment can own. M4's
`agent` schema is the next one likely to qualify. Adding to it is a decision someone makes;
leaving it at one name would have made the second such migration a surprise instead.
"""

SCENARIO_SCHEMA = "scenario_run"
CLEAN_SCHEMA = "clean_run"
"""Two copies of §5.2's schema in one database, one run in each.

Five of §3.5's eight make a claim with no meaning without a contrast group -- "carrier 7
stands out **from what**", "the verdicts did not move **compared to what**" -- and the
contrast is the same run with the fault left out. Two schemas rather than two databases,
so the comparison is a query across them rather than two connections and a join in Python.

**The clean schema is a property of this proof and never of a deployment.** A real history
holds one run, which is why scenario 3's `gap` consequence was dropped from the log rather
than rewritten as a paired claim: that one needed a twin *and* had no separation without
one. So each of the five has a form the run itself supports, and the twin either
calibrates the bar (4, 5, 7) or turns a statistical statement into an exact one (6, 8).

Scenario 8 is where that rule was stated and not kept: both halves of its difference read
this schema and there was no single-run form at all until `_one_part_only` gained one --
exactly one gapped part over the buffer-transit window a single press can reach. The twin
now says *which* part and that a clean run's identical window holds none, which is worth
having and is not what the claim rests on.
"""

DEFECT_SCORE_THRESHOLD = 0.5
"""`analysis.config.Settings.defect_class_threshold`. The score above which a class in
§3.4's vector counts as seen, so the queries below select parts the way
`/inspection/stats` does rather than by a rule of this file's own."""

SIGMA_BAR = 5.0
"""How far a windowed class rate must stand out from the same run's rate outside the
window to count as risen (`CLASS_RATE_RISES`), and how far a stream must fall to count as
fallen (`STREAM_FALLS`).

Measured over 20 h at the shipped settings: scenario 7's lot reaches **10.9 sigma** and
the identical window on a clean run reaches **2.3**, because 0.27 % of 500 parts is 1.35
expected gaps and four is where the baseline lands. Five sits between them with a factor
of two either side, and the pair of measurements is the point -- "more frequent" is
cleared by both runs and distinguishes nothing. Scenario 3's fall is 10.7 sigma.
"""

MIX_BAR = 2.0
"""How far the weakest named class must outweigh the strongest class the fault does not
name (`CLASS_MIX_SHIFTS`) -- and the same bar the clean twin must **not** clear.

One bar for both sides, because that is the claim. Measured over the 20 h this proof runs,
scenario 5 reaches **2.95x** and its clean twin **0.84x**. An earlier form asserted only
`clean < 1.0`, which is structurally true and worth less the longer the run: six classes
drawn at one baseline put the smallest of two just below the largest of four and closer to
it with every part -- 0.588 at 8 h, 0.696 at 12 h, 0.838 at 20 h, 0.875 at 33 h. What has
to be true is that a clean run does not satisfy the scenario's own claim.
"""

CONCENTRATION_BAR = 3.0
"""The leave-one-out Cohen's *d* a scoped carrier must reach (`CLASS_CONCENTRATES`).

`test_noise` is where this ratio is set and argued; here it is the bar a query has to
clear. Measured out of `inspection_results` at the production depth, over the parts after
the injection: carrier 7 reaches **d = +5.92** while the worst *other* carrier in the same
run reaches +1.25 and the worst carrier of a clean run reaches **+2.57** -- which is why
the bar is also "twice the best rival", and why the naive top-carrier query is asserted to
answer a clean run too. (`test_noise` quotes +5.84 and +2.52 for the same statistic over
every part rather than over the window, drawn without a line.)
"""

PRODUCTION_SECONDS = Settings().history_depth_hours * 3600.0
"""The depth the shipped history has. Read from `Settings` rather than written as 33
hours, so a deployment that shortens the history shortens this with it."""


def _code_of(browse_name: str) -> str:
    """`"S2_Joining"` -> `"S2"`, the way `TopologyDiscovery.SplitBrowseName` does it.

    A consequence names a station by its §4.1 browse name because that is what the plant
    publishes; §5.2's `stations` table is keyed on the code. The conversion has to happen
    somewhere, and doing it here rather than storing browse names is what keeps the
    queries below the ones an analysis would write.
    """
    code, _, _ = browse_name.partition("_")
    return code


def _name_of(browse_name: str) -> str:
    _, _, name = browse_name.partition("_")
    return name or browse_name


# --- the database ------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        # str(), because testcontainers ships no py.typed and mypy cannot take its word
        # for the return type -- see mypy.ini's [mypy-testcontainers.*] override.
        url = str(container.get_connection_url())
        migrations = sorted(MIGRATIONS.glob("*.sql"))
        # An empty glob would create two empty schemas and every count below would be
        # zero over zero rows -- the "assertion trivially true over an empty list" this
        # project keeps finding, and the shape this very path was first written with
        # (`parents[4]`, one directory above the repository).
        assert migrations, f"no migration found under {MIGRATIONS}"
        # Named so a rename fails here rather than silently re-including one below.
        assert OWNERSHIP_MIGRATIONS <= {migration.name for migration in migrations}
        migrations = [m for m in migrations if m.name not in OWNERSHIP_MIGRATIONS]
        with psycopg.connect(url) as conn:
            for schema in (SCENARIO_SCHEMA, CLEAN_SCHEMA):
                conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                conn.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
                )
                for migration in migrations:
                    conn.execute(migration.read_text())
            conn.commit()
        yield url


@pytest.fixture
def database(postgres_url: str) -> Iterator[Connection[tuple[object, ...]]]:
    """A connection with both schemas emptied.

    The table list is read from the catalogue rather than written down, for the reason
    `MIGRATIONS` is read from the directory: a table a later migration adds and this
    forgot would carry one scenario's rows into the next, and the failure would land on
    whichever test ran second.
    """
    with psycopg.connect(postgres_url) as conn:
        for schema in (SCENARIO_SCHEMA, CLEAN_SCHEMA):
            names = [
                str(row[0])
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,)
                ).fetchall()
            ]
            conn.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(schema, name) for name in names)
                )
            )
        conn.commit()
        yield conn


# --- the plant's published output, in §5.2's shape -----------------------------------------


def _load(
    conn: Connection[tuple[object, ...]], schema: str, run: Run, settings: Settings
) -> None:
    """Write one run's published output into `schema`.

    **This is not a second gateway and it must not become one.** It writes what the four
    station node sets published -- the numeric streams, the PackML states and their
    reasons, S3's verdicts and §4.2's alarms -- into the columns those values belong to,
    and it performs exactly the two merges the *migration files themselves* state as the
    schema's rule: `State` and `StateReason` arrive as two writes on one instant and
    `002_m2a.sql` keys them into one row; an alarm arrives as three lifecycle events and
    `004_m2c.sql` keys them on (station, code, raised instant). Neither is inferred from
    reading `PostgresWriter`, and nothing here reproduces its ordering, its batching, its
    upsert precedence or its out-of-order repair. A Python copy of those would go wrong by
    reporting rows the real gateway would never write, which is the objection
    `measurements/authenticity/README.md` already records against repairing
    `run_r1_r2.py` that way.

    Deliberately **not** written: M2b's identity tables beyond the `assemblies` row
    `inspection_results` is keyed beside, and `buffer_levels`. No consequence in §3.5
    observes either, and writing them would be duplication bought for nothing.

    The event field names are read off `simulator.events` rather than spelled here, so a
    field renamed on the plant's side breaks this loudly instead of leaving a column
    silently null -- which is the failure mode `events.py`'s own docstring opens with.
    """
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    for position, browse in enumerate(STATION_SIGNALS, start=1):
        conn.execute(
            "INSERT INTO stations (code, name, position_in_line) VALUES (%s, %s, %s)",
            (_code_of(browse), _name_of(browse), position),
        )
    stations = {
        _text(code): _int(ident)
        for code, ident in conn.execute("SELECT code, id FROM stations").fetchall()
    }
    for buffer_id, upstream, downstream in BUFFERS:
        conn.execute(
            "INSERT INTO buffers (code, upstream_station_id, downstream_station_id,"
            " capacity) VALUES (%s, %s, %s, %s)",
            (
                buffer_id,
                stations[_code_of(upstream)],
                stations[_code_of(downstream)],
                settings.buffer_capacity,
            ),
        )
    buffers = {
        _text(code): _int(ident)
        for code, ident in conn.execute("SELECT code, id FROM buffers").fetchall()
    }
    for carrier in range(settings.carrier_count):
        conn.execute("INSERT INTO carriers (id) VALUES (%s)", (carrier,))

    _load_signals(conn, run, stations)
    _load_state_changes(conn, run, stations, buffers)
    _load_inspection(conn, run, stations)
    _load_alarms(conn, run, stations)
    conn.commit()


def _load_signals(
    conn: Connection[tuple[object, ...]], run: Run, stations: dict[str, int]
) -> None:
    """§4.1's numeric streams.

    `State` and `StateReason` are the only two writes that are not numbers and the only
    two routed elsewhere. The **type** is what decides that, not a list of names kept in
    step: `signals.value` is DOUBLE PRECISION, so a string cannot land there whatever it
    is called.
    """
    rows: dict[tuple[int, str, datetime], float] = {}
    for browse, nodes in run.nodes.items():
        station = stations[_code_of(browse)]
        for signal, at, value in nodes.writes:
            if isinstance(value, str):
                continue
            # First write wins, which is what `ON CONFLICT (station_id, signal,
            # source_ts) DO NOTHING` does to the page-boundary duplicate the real ingest
            # path meets.
            rows.setdefault((station, signal, at), float(value))
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY signals (station_id, signal, source_ts, value) FROM STDIN"
        ) as cp,
    ):
        for (station, signal, at), value in rows.items():
            cp.write_row((station, signal, at, value))


def _load_state_changes(
    conn: Connection[tuple[object, ...]],
    run: Run,
    stations: dict[str, int],
    buffers: dict[str, int],
) -> None:
    """§3.3's transitions, State and StateReason keyed into one row per (station, instant).

    `from_state` is the station's own previously published state, which the plant knows
    exactly: there is no history horizon in process, so the null `002_m2a.sql` allows for
    "the first state seen after a connect" appears only on the genuinely first one.

    An empty reason is stored as NULL, which is the rule that migration states in as many
    words -- it is what the node reads when a station is not suspended, and stored as text
    it would make every unsuspend look like a condition with a nameless cause.
    """
    for browse, nodes in run.nodes.items():
        station = stations[_code_of(browse)]
        states = {
            at: str(value) for signal, at, value in nodes.writes if signal == "State"
        }
        reasons = {
            at: str(value)
            for signal, at, value in nodes.writes
            if signal == "StateReason"
        }
        previous: str | None = None
        for at in sorted(states | reasons):
            to_state = states.get(at)
            reason = reasons.get(at) or None
            conn.execute(
                "INSERT INTO state_changes"
                " (station_id, source_ts, from_state, to_state, reason, reason_buffer_id)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    station,
                    at,
                    previous,
                    to_state,
                    reason,
                    None if reason is None else buffers.get(reason.partition(":")[2]),
                ),
            )
            previous = to_state or previous


def _payloads(
    run: Run, browse: str, event: EventType
) -> list[tuple[datetime, dict[str, object]]]:
    """Every payload of one event type on one station, with the instant it was triggered.

    §4.2 is explicit that the instant is BaseEventType's `Time` and not a field of the
    plant's own, which is why it comes from beside the payload rather than out of it.
    """
    return [
        (at, fields)
        for name, at, fields in run.nodes[browse].events
        if name == event.name
    ]


def _load_inspection(
    conn: Connection[tuple[object, ...]], run: Run, stations: dict[str, int]
) -> None:
    """§3.4's verdict as S3 published it: the six-score vector and the scalar beside it.

    There is no `defect_class` here on purpose. §5.2's widened row has no scalar class and
    every row's has been NULL since M2b -- a loader that filled it would make a column
    nothing reads look alive.
    """
    for browse in INSPECTION_RESULT.stations:
        station = stations[_code_of(browse)]
        for at, fields in _payloads(run, browse, INSPECTION_RESULT):
            serial = str(fields["AssemblySerial"])
            carrier = int(_float(fields["CarrierId"]))
            image = fields["Image"]
            conn.execute(
                "INSERT INTO assemblies (serial, carrier_id) VALUES (%s, %s)"
                " ON CONFLICT (serial) DO NOTHING",
                (serial, carrier),
            )
            conn.execute(
                "INSERT INTO inspection_results (assembly_serial, source_ts, station_id,"
                " result, confidence, model_version, image_ref, carrier_id,"
                " defect_classes, confidences)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    serial,
                    at,
                    station,
                    str(fields["Disposition"]),
                    _float(fields["Confidence"]),
                    str(fields["ModelVersion"]),
                    serial if image is not None else None,
                    carrier,
                    [str(name) for name in _sequence(fields["DefectClasses"])],
                    [_float(score) for score in _sequence(fields["Confidences"])],
                ),
            )
            if image is not None:
                conn.execute(
                    "INSERT INTO inspection_images (assembly_serial, bytes)"
                    " VALUES (%s, %s)",
                    (serial, _image(image)),
                )


def _load_alarms(
    conn: Connection[tuple[object, ...]], run: Run, stations: dict[str, int]
) -> None:
    """§4.2's three lifecycle events into one row each, keyed on (station, code, raised).

    **Which transition an event is comes from the two flags** and never from which columns
    happen to be filled: active and unacknowledged is the raise, active and acknowledged
    is the acknowledgement, inactive is the clear. That is the flags' plain meaning, and
    `events.ALARM`'s own docstring is where it is stated.
    """
    for browse in ALARM.stations:
        station = stations[_code_of(browse)]
        for at, fields in _payloads(run, browse, ALARM):
            raised_at = fields["AlarmRaisedAt"]
            assert isinstance(raised_at, datetime)
            code = str(fields["AlarmCode"])
            conn.execute(
                "INSERT INTO alarms (station_id, code, text, severity, raised_at)"
                " SELECT %s, %s, %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM alarms"
                "  WHERE station_id = %s AND code = %s AND raised_at = %s)",
                (
                    station,
                    code,
                    str(fields["AlarmText"]),
                    int(_float(fields["AlarmSeverity"])),
                    raised_at,
                    station,
                    code,
                    raised_at,
                ),
            )
            active = bool(fields["AlarmActive"])
            if active and not bool(fields["AlarmAcknowledged"]):
                continue
            conn.execute(
                sql.SQL(
                    "UPDATE alarms SET {col} = COALESCE({col}, %s)"
                    " WHERE station_id = %s AND code = %s AND raised_at = %s"
                ).format(col=sql.Identifier("acked_at" if active else "cleared_at")),
                (at, station, code, raised_at),
            )


def _int(value: object) -> int:
    """One integer column or event field, **narrowed** rather than coerced.

    `Connection[tuple[object, ...]]` is what keeps `Any` out of these queries -- mypy's
    --strict does not ban an explicit one, so `disallow_any_explicit` does -- and the
    price is that every column arrives as `object`. Narrowing with an assert rather than
    calling `int()` on it is what keeps a column that is not a number a failure here
    instead of a plausible value further down.
    """
    assert isinstance(value, int)
    return value


def _float(value: object) -> float:
    assert isinstance(value, int | float)
    return float(value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _sequence(value: object) -> Sequence[object]:
    assert isinstance(value, tuple | list)
    return value


def _image(value: object) -> bytes:
    assert isinstance(value, bytes)
    return value


# --- running one scenario and writing its ground truth -------------------------------------


def _recording_truth(log: GroundTruthLog) -> ProduceFactory:
    """The plant's own truth as the verdict, with every part written to `log` first.

    The two lines `ground_truth.recording` performs, with `client.produce` replaced by
    `truth_outcome` -- and the reason it is not `recording` itself is measured: `produce`
    renders a PNG per part at **4.18 ms**, which is 83 s for the 19,725 parts scenario 4
    needs at the production depth, on each of two runs. Scenario 6 *is* about the image
    and uses the real `recording` over the real client; everywhere else the classifier's
    own two error rates (D7) would put noise that is not the plant's in front of every
    measurement, which is what `truth_outcome` documents.
    """

    def factory(client: InspectionClient) -> ProduceFn:
        async def produce(
            part_id: str, carrier_id: int, joining_work: float, at: datetime
        ) -> PartOutcome:
            log.record_part(
                part_id,
                carrier_id,
                at,
                client.truth_by_lane(part_id, carrier_id, joining_work, at),
            )
            return truth_outcome(
                client.truth_for(part_id, carrier_id, joining_work, at)
            )

        return produce

    return factory


def _scenario_injections(
    records: Sequence[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    """Every injection the log attributes to a scenario.

    `source == "scenario"` and never "has consequences": §3.7's panel writes an operator's
    injection with an empty list, and selecting on emptiness would read "nobody wrote down
    what should follow" as "this claimed nothing" -- two different statements a later
    milestone would then score identically.
    """
    return tuple(
        record
        for record in records
        if record["record"] == "injection" and record["source"] == "scenario"
    )


@dataclass(frozen=True)
class Proof:
    """One scenario's run, its ground-truth log read back off disk, and the database."""

    conn: Connection[tuple[object, ...]]
    run: Run
    records: tuple[dict[str, object], ...]


async def _prove(
    conn: Connection[tuple[object, ...]],
    number: int,
    seconds: float,
    tmp_path: Path,
    *,
    twin: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    produce_with: Callable[[GroundTruthLog], ProduceFactory] = _recording_truth,
) -> Proof:
    """Run scenario `number`, write its ground truth, and load the result into Postgres.

    One clock for the log and the run: `SimulatedClock.history_start` is `boot - depth`
    off the wall clock, so two of them are microseconds apart and every instant the log
    records would be an instant the run did not start at.

    The clean twin runs on the same origin and writes **no** ground truth. It is this
    proof's contrast group and not a second run of the plant, and a second set of part
    records interleaved into one log would describe a run that never happened.
    """
    settings = Settings()
    item = scenario(number, settings)
    clock = new_clock(settings)
    path = tmp_path / f"scenario-{number}.jsonl"

    with open_log(path, settings, clock, item) as log:
        if twin:
            clean, run = await paired_runs(
                number,
                seconds,
                settings,
                clock=clock,
                transport=transport,
                produce_with=produce_with(log),
            )
        else:
            clean = None
            run = await scenario_run(
                number,
                seconds,
                settings,
                clock=clock,
                transport=transport,
                produce_with=produce_with(log),
            )

    _load(conn, SCENARIO_SCHEMA, run, settings)
    if clean is not None:
        _load(conn, CLEAN_SCHEMA, clean, settings)

    records = tuple(
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    )
    assert any(record["record"] == "part" for record in records), (
        "the log holds no part record, so the line inspected nothing and every rate "
        "below would be measured over an empty run"
    )
    return Proof(conn, run, records)


# --- the checkers: one per expectation, each SQL against the loaded run ---------------------


@dataclass(frozen=True)
class Claim:
    """One consequence as the ground-truth log records it, with the fault's own facts."""

    observable: str
    expect: str
    subjects: tuple[str, ...]
    scope: str
    within_seconds: float | None
    at: datetime
    until: datetime | None
    params: dict[str, float]
    origin: datetime

    def elapsed(self, at: datetime) -> float:
        return (at - self.at).total_seconds()

    @property
    def carrier(self) -> int:
        key, _, value = self.scope.partition("=")
        assert key == "carrier", f"{self.scope!r} names no carrier"
        return int(value)


Checker = Callable[[Connection[tuple[object, ...]], Claim], str]


def _station_id(conn: Connection[tuple[object, ...]], schema: str, browse: str) -> int:
    row = conn.execute(
        sql.SQL("SELECT id FROM {}.stations WHERE code = %s").format(
            sql.Identifier(schema)
        ),
        (_code_of(browse),),
    ).fetchone()
    assert row is not None, f"{browse} has no row in {schema}.stations"
    return _int(row[0])


def _in_order(
    conn: Connection[tuple[object, ...]], claim: Claim, prefix: str, feeding: bool
) -> str:
    """The shared half of `suspended_in_order` and `blocked_in_order`.

    **The order is the claim and the ordering is strict.** A line with no buffers at all
    would suspend every station at one instant, and `== sorted(...)` is non-*decreasing*
    and passes on exactly that -- the assertion this milestone has already had to correct
    once. The measured spacings clear it comfortably: **19.5 / 41.9 / 46.1 s** downstream
    and **30.0 / 37.0 / 49.8 s** upstream, read off `state_changes`.

    Each station must have stopped on the buffer **adjacent to it in the direction the
    condition travels**, resolved through `state_changes.reason_buffer_id` into `buffers`
    -- so the chain is checked against the line's own topology rather than against three
    literal buffer names that would go on passing after the line was re-plumbed.

    The row read is the station's **last** settled transition and not its first: a running
    line suspends on its own buffers a few percent of the time, so "the first time S2
    starved" is answered by an ordinary micro-stop in the warmup. A propagation chain is
    the one that does not clear.
    """
    assert claim.within_seconds is not None, f"{claim.expect} needs a window to hold in"
    column = "downstream_station_id" if feeding else "upstream_station_id"
    reached: list[tuple[str, float]] = []
    for browse in claim.subjects:
        station = _station_id(conn, SCENARIO_SCHEMA, browse)
        row = conn.execute(
            sql.SQL(
                "SELECT sc.to_state, sc.reason, sc.source_ts, b.code, b.{col}"
                " FROM {schema}.state_changes_settled sc"
                " LEFT JOIN {schema}.buffers b ON b.id = sc.reason_buffer_id"
                " WHERE sc.station_id = %s ORDER BY sc.source_ts DESC LIMIT 1"
            ).format(
                col=sql.Identifier(column), schema=sql.Identifier(SCENARIO_SCHEMA)
            ),
            (station,),
        ).fetchone()
        assert row is not None, f"{browse} never changed state at all"
        assert str(row[0]) == State.SUSPENDED.value, (
            f"{browse} ended the run {row[0]}, not Suspended"
        )
        reason = None if row[1] is None else str(row[1])
        assert reason is not None and reason.startswith(f"{prefix}:"), (
            f"{browse} ended on {reason!r}, which is not a {prefix} condition"
        )
        assert row[3] is not None, (
            f"{browse}'s reason {reason!r} resolved to no buffer, so the chain names "
            "nothing the line is built from"
        )
        assert _int(row[4]) == station, (
            f"{browse} stopped on {row[3]}, which is not the buffer beside it in the "
            "direction the condition travels"
        )
        at = row[2]
        assert isinstance(at, datetime)
        reached.append((browse, round(claim.elapsed(at), 1)))

    assert all(
        earlier[1] < later[1] for earlier, later in itertools.pairwise(reached)
    ), f"the chain did not reach the stations in order: {reached}"
    assert reached[0][1] > 0, f"the chain started before the injection: {reached}"
    assert reached[-1][1] <= claim.within_seconds, (
        f"the chain took {reached[-1][1]} s and the ground-truth log claims "
        f"{claim.within_seconds} s: {reached}"
    )
    return " -> ".join(f"{name} +{offset} s" for name, offset in reached)


def _suspended_in_order(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    return _in_order(conn, claim, "starved", feeding=True)


def _blocked_in_order(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    return _in_order(conn, claim, "blocked", feeding=False)


def _stream_stats(
    conn: Connection[tuple[object, ...]],
    claim: Claim,
    lo: datetime,
    hi: datetime | None,
) -> tuple[float, float, int]:
    browse, _, signal = claim.observable.partition(".")
    row = conn.execute(
        sql.SQL(
            "SELECT avg(value), coalesce(stddev_samp(value), 0), count(*)"
            " FROM {}.signals WHERE station_id = %s AND signal = %s"
            "  AND source_ts >= %s AND (%s::timestamptz IS NULL OR source_ts < %s)"
        ).format(sql.Identifier(SCENARIO_SCHEMA)),
        (_station_id(conn, SCENARIO_SCHEMA, browse), signal, lo, hi, hi),
    ).fetchone()
    assert row is not None and _int(row[2]) > 0, (
        f"{claim.observable} has no row in [{lo}, {hi}): the stream is unmeasurable "
        "there, so nothing computed from it would mean anything"
    )
    return _float(row[0]), _float(row[1]), _int(row[2])


def _stream_falls(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The stream's level after the ramp is below its level before the injection, by more
    than its own part-to-part spread can account for.

    Measured on scenario 3: `JoiningForcePeak` 4214.3 N -> 3791.9 N against a 39.4 N
    spread -- **10.7 sigma**, and the number scenario 7's `STREAM_STABLE` is the other side
    of.
    """
    ramp = timedelta(seconds=claim.params.get("ramp_seconds", 0.0))
    before, spread, n_before = _stream_stats(conn, claim, claim.origin, claim.at)
    after, _, n_after = _stream_stats(conn, claim, claim.at + ramp, None)
    assert spread > 0, "the stream never moved at all, so a fall cannot be measured"
    fall = before - after
    sigmas = fall / spread
    assert sigmas >= SIGMA_BAR, (
        f"{claim.observable} fell {fall:.1f} against a {spread:.1f} spread "
        f"({sigmas:.1f} sigma) over {n_before} rows before and {n_after} after"
    )
    magnitude = abs(claim.params.get("newtons", 0.0))
    assert fall > 0.5 * magnitude, (
        f"the stream fell {fall:.1f} on a fault of magnitude {magnitude}: the fault is "
        "not reaching the published value"
    )
    return f"{before:.1f} -> {after:.1f} ({sigmas:.1f} sigma of {spread:.1f})"


def _stream_stable(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The stream does **not** move across the window, while the class rate does.

    §3.5 row 7's whole reason for existing: its symptom is row 3's symptom, and the force
    is the only thing that tells them apart -- so a scenario that also drifted the force
    would have quietly become scenario 3. Measured: the mean moves **0.059 sigma** of the
    stream's own part-to-part spread against row 3's 10.7 -- a factor of 180 on the one
    stream that tells the two apart.
    """
    assert claim.until is not None, (
        "a stream cannot be stable across a window the fault never ends"
    )
    outside, spread, n_outside = _stream_stats(conn, claim, claim.origin, claim.at)
    inside, _, n_inside = _stream_stats(conn, claim, claim.at, claim.until)
    assert spread > 0, "the stream never moved at all, so stability says nothing"
    shift = abs(inside - outside) / spread
    assert shift < 0.25, (
        f"{claim.observable} moved {shift:.2f} sigma across the window ({n_outside} rows "
        f"before, {n_inside} inside): this scenario has become scenario 3, and the one "
        "thing that distinguishes it is gone"
    )
    return f"{shift:.3f} sigma of {spread:.1f} over {n_inside} rows"


def _alarm_raised(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The named station has a row in `alarms` raised inside the claimed window.

    A claim about the plant and never about the diagnosis: §3.3 rules out "the first
    station that raised an alarm" as a root cause precisely because the simulator produces
    it, so what is asserted is that row 3's alarm half happened.
    """
    assert claim.within_seconds is not None
    (browse,) = claim.subjects
    row = conn.execute(
        sql.SQL(
            "SELECT code, text, severity, raised_at FROM {}.alarms"
            " WHERE station_id = %s AND raised_at >= %s ORDER BY raised_at LIMIT 1"
        ).format(sql.Identifier(SCENARIO_SCHEMA)),
        (_station_id(conn, SCENARIO_SCHEMA, browse), claim.at),
    ).fetchone()
    assert row is not None, (
        f"{browse} raised no alarm after the injection, and §3.5's row ends in one"
    )
    raised_at = row[3]
    assert isinstance(raised_at, datetime)
    elapsed = claim.elapsed(raised_at)
    assert 0 < elapsed <= claim.within_seconds, (
        f"{row[0]} was raised {elapsed:.0f} s after the injection and the ground-truth "
        f"log claims {claim.within_seconds:.0f} s"
    )
    return f"{row[0]} ({row[1]}, severity {row[2]}) +{elapsed:.0f} s"


def _station_aborts(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The named station reaches `Aborted` inside the window, **after its own alarm**.

    The ordering is half the claim and the two `within_seconds` cannot express it: an
    abort that preceded its own alarm would be a station shut down with nothing naming
    what for. Measured: `Aborted` is published **0.5 s** after A-207 is raised -- the
    alarm is measured on the cycle that pressed the part, `Aborting` is published first and
    `Aborted` a `state_transition_seconds` later, and both are in `state_changes`.
    """
    assert claim.within_seconds is not None
    (browse,) = claim.subjects
    station = _station_id(conn, SCENARIO_SCHEMA, browse)
    row = conn.execute(
        sql.SQL(
            "SELECT source_ts FROM {}.state_changes_settled"
            " WHERE station_id = %s AND to_state = %s AND source_ts >= %s"
            " ORDER BY source_ts LIMIT 1"
        ).format(sql.Identifier(SCENARIO_SCHEMA)),
        (station, State.ABORTED.value, claim.at),
    ).fetchone()
    assert row is not None, (
        f"{browse} never reached Aborted, so the alarm shut nothing down"
    )
    aborted_at = row[0]
    assert isinstance(aborted_at, datetime)
    alarm = conn.execute(
        sql.SQL(
            "SELECT min(raised_at) FROM {}.alarms"
            " WHERE station_id = %s AND raised_at >= %s"
        ).format(sql.Identifier(SCENARIO_SCHEMA)),
        (station, claim.at),
    ).fetchone()
    assert alarm is not None and isinstance(alarm[0], datetime), (
        f"{browse} aborted with no alarm in the table, so the shutdown names nothing"
    )
    assert aborted_at > alarm[0], (
        f"{browse} aborted at {aborted_at} before its own alarm at {alarm[0]}"
    )
    elapsed = claim.elapsed(aborted_at)
    assert elapsed <= claim.within_seconds, (
        f"{browse} aborted {elapsed:.0f} s after the injection and the log claims "
        f"{claim.within_seconds:.0f} s"
    )
    return (
        f"Aborted +{elapsed:.0f} s, "
        f"{(aborted_at - alarm[0]).total_seconds():.1f} s after the alarm"
    )


def _class_counts(
    conn: Connection[tuple[object, ...]],
    schema: str,
    classes: Sequence[str],
    lo: datetime,
    hi: datetime | None,
) -> tuple[int, int]:
    """`(parts carrying one of `classes`, parts inspected)` in a window.

    The membership test is `/inspection/stats`' own: `unnest` of the two parallel arrays
    together so a class stays paired with its own score, and the threshold is what selects
    -- not a scalar column, which §3.4's vector replaced and which has been NULL since
    M2b.
    """
    row = conn.execute(
        sql.SQL(
            "SELECT count(*) FILTER (WHERE EXISTS ("
            "  SELECT 1 FROM unnest(r.defect_classes, r.confidences) AS scored(c, score)"
            "  WHERE scored.c = ANY(%s) AND scored.score >= %s)), count(*)"
            " FROM {}.inspection_results r"
            " WHERE r.source_ts >= %s AND (%s::timestamptz IS NULL OR r.source_ts < %s)"
        ).format(sql.Identifier(schema)),
        (list(classes), DEFECT_SCORE_THRESHOLD, lo, hi, hi),
    ).fetchone()
    assert row is not None
    return _int(row[0]), _int(row[1])


def _sigmas(hits: int, seen: int, baseline: float) -> float:
    """How far `hits` in `seen` parts stands above `baseline`, in Poisson sigmas.

    Poisson rather than a rate ratio, because the window holds a handful of events and a
    ratio says nothing about how many: 4 gaps where 1.35 are expected is a 3x ratio and
    2.3 sigma, while 14 where 1.35 are expected is 10x and 10.9 sigma. The first is a
    clean run.
    """
    expected = baseline * seen
    assert expected > 0, (
        "the rate outside the window is exactly zero, so there is no baseline to stand "
        "out from and any event at all would satisfy this"
    )
    return (hits - expected) / math.sqrt(expected)


def _rate_rise(
    conn: Connection[tuple[object, ...]], schema: str, claim: Claim
) -> tuple[float, int, float]:
    assert claim.until is not None
    inside_hits, inside_seen = _class_counts(
        conn, schema, claim.subjects, claim.at, claim.until
    )
    before_hits, before_seen = _class_counts(
        conn, schema, claim.subjects, claim.origin, claim.at
    )
    after_hits, after_seen = _class_counts(
        conn, schema, claim.subjects, claim.until, None
    )
    outside_seen = before_seen + after_seen
    assert inside_seen > 0 and outside_seen > 0, (
        "the window or its complement holds no inspected part at all"
    )
    baseline = (before_hits + after_hits) / outside_seen
    return _sigmas(inside_hits, inside_seen, baseline), inside_hits, baseline


def _class_rate_rises(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The named classes are **significantly** more frequent inside the window than in the
    same run outside it -- and the identical measurement on a clean run is not.

    The clean half is what makes the bar something rather than an arbitrary number.
    Measured over 20 h: scenario 7's lot reaches 10.9 sigma and the clean twin's identical
    window reaches 2.3, because 0.27 % of 500 parts is 1.35 expected gaps and the baseline
    lands on four of them. A bar of "more frequent" is cleared by both.
    """
    sigmas, hits, baseline = _rate_rise(conn, SCENARIO_SCHEMA, claim)
    assert sigmas >= SIGMA_BAR, (
        f"{list(claim.subjects)} reached {hits} inside the window against a baseline of "
        f"{baseline:.4%} -- {sigmas:.1f} sigma, where a clean line's own baseline already "
        f"reaches a few"
    )
    clean, clean_hits, _ = _rate_rise(conn, CLEAN_SCHEMA, claim)
    assert clean < SIGMA_BAR, (
        f"the same window on a run with no fault in it reaches {clean:.1f} sigma "
        f"({clean_hits} parts): this claim is satisfied by a clean line, so it asserts "
        "the baseline rather than the scenario"
    )
    return (
        f"{hits} inside at {sigmas:.1f} sigma, clean twin {clean_hits} at {clean:.1f}"
    )


def _per_class_rates(
    conn: Connection[tuple[object, ...]], schema: str, lo: datetime
) -> dict[str, float]:
    total = conn.execute(
        sql.SQL(
            "SELECT count(*) FROM {}.inspection_results WHERE source_ts >= %s"
        ).format(sql.Identifier(schema)),
        (lo,),
    ).fetchone()
    assert total is not None and _int(total[0]) > 0, (
        f"{schema} holds no verdict after {lo}"
    )
    rows = conn.execute(
        sql.SQL(
            "SELECT scored.c, count(DISTINCT r.assembly_serial)"
            " FROM {}.inspection_results r,"
            "      unnest(r.defect_classes, r.confidences) AS scored(c, score)"
            " WHERE r.source_ts >= %s AND scored.score >= %s GROUP BY scored.c"
        ).format(sql.Identifier(schema)),
        (lo, DEFECT_SCORE_THRESHOLD),
    ).fetchall()
    found = {_text(name): _int(count) / _int(total[0]) for name, count in rows}
    return {name: found.get(name, 0.0) for name in DEFECT_CLASSES}


def _class_mix_shifts(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """Every named class outweighs every class the fault does not name, and on a clean run
    it does not.

    Scenario 5's, and the only shape a fault with no `until` can support: there is no
    outside window to compare against, and the 300-part warmup before the injection
    carries two defects. The four classes the fault does not name are the contrast group,
    and they are in the same rows.

    Measured over the 20 h this proof runs: `missing_part` and `contamination` reach
    0.96 % and 0.94 % against the other four at 0.24-0.32 %, so the weakest named class is
    **2.95x** the strongest unnamed one against a clean twin's **0.84x** on the identical
    query. At the production depth the two are 3.20x and 0.875x.
    """
    named = set(claim.subjects)
    rates = _per_class_rates(conn, SCENARIO_SCHEMA, claim.at)
    highest_other = max(rate for name, rate in rates.items() if name not in named)
    assert highest_other > 0, (
        "no class outside the named ones scored above the threshold at all, so there is "
        "no contrast group and this would pass on a plant that draws nothing else"
    )
    ratio = min(rates[name] for name in named) / highest_other
    assert ratio >= MIX_BAR, (
        f"the weakest named class is {ratio:.2f}x the strongest unnamed one: "
        f"{ {name: round(rate, 5) for name, rate in rates.items()} }"
    )
    clean = _per_class_rates(conn, CLEAN_SCHEMA, claim.at)
    clean_ratio = min(clean[name] for name in named) / max(
        rate for name, rate in clean.items() if name not in named
    )
    assert clean_ratio < MIX_BAR, (
        f"a run with no contamination in it already clears the same bar at "
        f"{clean_ratio:.2f}x the rest, so the mix says nothing about the fault"
    )
    return f"{ratio:.2f}x the rest, clean twin {clean_ratio:.2f}x"


def _rates_by_carrier(
    conn: Connection[tuple[object, ...]],
    schema: str,
    classes: Sequence[str],
    lo: datetime,
) -> dict[int, float]:
    rows = conn.execute(
        sql.SQL(
            "SELECT r.carrier_id, count(*) FILTER (WHERE EXISTS ("
            "  SELECT 1 FROM unnest(r.defect_classes, r.confidences) AS scored(c, score)"
            "  WHERE scored.c = ANY(%s) AND scored.score >= %s)), count(*)"
            " FROM {}.inspection_results r WHERE r.source_ts >= %s"
            " GROUP BY r.carrier_id"
        ).format(sql.Identifier(schema)),
        (list(classes), DEFECT_SCORE_THRESHOLD, lo),
    ).fetchall()
    assert rows, f"{schema} holds no verdict to group by carrier"
    return {_int(carrier): _int(hits) / _int(seen) for carrier, hits, seen in rows}


def _effect(rates: dict[int, float], carrier: int) -> float:
    """Cohen's *d* for `carrier` against the rest -- **leave-one-out**: the mean and the
    spread it is measured against exclude the carrier being tested, because a carrier
    genuinely out of family drags a pool statistic it is inside towards itself. Every *d*
    in this file and in `test_noise` is this one, so they compare directly."""
    others = [rate for index, rate in rates.items() if index != carrier]
    spread = statistics.stdev(others)
    assert spread > 0, (
        "every other carrier has the identical rate, which is not a plant"
    )
    return (rates[carrier] - statistics.fmean(others)) / spread


def _class_concentrates(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The named classes concentrate on the scoped carrier, measured against the pool's
    own spread -- and a `GROUP BY` is *shown* not to suffice rather than said not to.

    Three assertions, and the third is what §3.5 row 4 turns on:

    * carrier 7 clears `CONCENTRATION_BAR` (measured **+5.92**);
    * it clears twice the best rival **in its own run** (+1.25), so nothing else comes
      near it;
    * it clears twice the best carrier of the **clean twin** (+2.57), and the clean run is
      asserted to have a top carrier of its own -- `ORDER BY rate DESC LIMIT 1` returns
      one whether or not anything is wrong, which is the whole reason a significance test
      is needed and that query is not one.
    """
    carrier = claim.carrier
    rates = _rates_by_carrier(conn, SCENARIO_SCHEMA, claim.subjects, claim.at)
    assert carrier in rates, f"carrier {carrier} rode no part in this run"
    effect = _effect(rates, carrier)
    rival = max(_effect(rates, other) for other in rates if other != carrier)

    clean = _rates_by_carrier(conn, CLEAN_SCHEMA, claim.subjects, claim.at)
    clean_worst = max(_effect(clean, other) for other in clean)
    clean_top = max(clean, key=lambda index: clean[index])

    assert effect > CONCENTRATION_BAR, (
        f"carrier {carrier} separates from the pack by only d={effect:.2f}: the answer "
        "is inside the noise"
    )
    assert effect > 2 * rival, (
        f"the best rival carrier in the same run reaches d={rival:.2f} against carrier "
        f"{carrier}'s {effect:.2f}"
    )
    assert effect > 2 * clean_worst, (
        f"a run with nothing wrong with it already has a carrier at d={clean_worst:.2f} "
        f"against {effect:.2f}: too close for a significance test to mean anything"
    )
    assert clean[clean_top] > 0, (
        "no carrier on the clean run carries these classes at all, so 'the top carrier "
        "is the culprit' has nothing to be wrong about here"
    )
    return (
        f"d={effect:.2f} vs rival {rival:.2f}, clean worst {clean_worst:.2f} "
        f"(a clean GROUP BY still answers carrier {clean_top})"
    )


def _mean_scores(
    conn: Connection[tuple[object, ...]], lo: datetime, hi: datetime | None
) -> dict[str, float]:
    """The mean score of each of §3.4's six classes over the **good** parts in a window.

    Good parts only, and it is the difference between measuring the decay and measuring
    which defects happened to occur. §3.4 says a good part is six low scores, so every row
    here contributes the baseline to all six and the population has no class structure at
    all. Include the rejects and one class per reject is boosted instead -- over the four
    rejects before this injection and the nine after it, the six ratios came out 0.074
    apart and said nothing about the lens.
    """
    rows = conn.execute(
        sql.SQL(
            "SELECT scored.c, avg(scored.score)"
            " FROM {}.inspection_results r,"
            "      unnest(r.defect_classes, r.confidences) AS scored(c, score)"
            " WHERE r.result = 'good'"
            "   AND r.source_ts >= %s AND (%s::timestamptz IS NULL OR r.source_ts < %s)"
            " GROUP BY scored.c"
        ).format(sql.Identifier(SCENARIO_SCHEMA)),
        (lo, hi, hi),
    ).fetchall()
    assert len(rows) == len(DEFECT_CLASSES), (
        f"{len(rows)} classes scored in [{lo}, {hi}) and §3.4's vector carries "
        f"{len(DEFECT_CLASSES)}: a good part is six low scores, not an absent vector"
    )
    return {_text(name): _float(mean) for name, mean in rows}


def _confidence_decays(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """Every named class's score falls, **together**, by the fouling's own factor.

    §3.4's softmax-impossible shape is what makes this expressible at all: six independent
    scores that do not sum to 1 can every one of them fall, which no distribution over six
    values can do. The ratio is asserted against the fault's own `factor` rather than
    against a number written here, so the assertion moves when the scenario does.
    """
    ramp = timedelta(seconds=claim.params.get("ramp_seconds", 0.0))
    factor = claim.params["factor"]
    before = _mean_scores(conn, claim.origin, claim.at)
    after = _mean_scores(conn, claim.at + ramp, None)
    ratios = {name: after[name] / before[name] for name in claim.subjects}
    for name, ratio in ratios.items():
        assert ratio < 1.0, f"{name} did not fall at all ({ratio:.3f})"
        assert abs(ratio - factor) < 0.05, (
            f"{name} fell to {ratio:.3f} of its score while the fault scales clarity to "
            f"{factor}: the decay is not proportional to the fouling"
        )
    spread = max(ratios.values()) - min(ratios.values())
    assert spread < 0.01, (
        f"the six classes fell {spread:.3f} apart rather than together: "
        f"{ {name: round(ratio, 4) for name, ratio in ratios.items()} }"
    )
    return f"all six to {statistics.fmean(ratios.values()):.3f} (factor {factor})"


_DISPOSED = sql.SQL("SELECT assembly_serial, result FROM {schema}.inspection_results")


def _scrap_rate_flat(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """The fouled run and its clean twin dispose of **the same parts the same way**.

    What this asserts about the plant is that `OPTICS_FOULING` touches the **image and
    nothing else**: the verdict comes from what the part genuinely carries, so a fault
    that also raised a defect propensity would move a disposition and this would fail.

    **Against the twin and not against a before-window, because the before-window cannot
    fail.** This compared the 300 parts of the warmup with the 595 after the ramp at three
    pooled standard errors, and the 300-part side is what sets the bar: 2.727 pp on a
    2.000 % baseline, which no amount of extra depth on the other side narrows. Measured
    by mutating `truth_by_lane` so the fouling also raised the defect propensity: at
    1/clarity**2 the reject rate went **2.000 % -> 6.218 %, 2.79 standard errors, and the
    old assertion passed**; at 1/clarity, 3.025 % and 0.90. The twin is a fixed draw
    against a fixed draw, so the comparison here is exact rather than statistical -- one
    part whose verdict moved is a failure, and those two mutations now move 43 and 19.

    What this does **not** establish is the classifier's half -- that a fouled lens costs
    certainty and not the verdict on a real image. That is
    `plant/inspection/tests/test_inspection.py::test_a_fouled_lens_costs_certainty_and_not_the_verdict`,
    on the real classifier, and it belongs there: the two workspaces may not import each
    other (§10.7).
    """
    factor = claim.params["factor"]
    scenario_side = _DISPOSED.format(schema=sql.Identifier(SCENARIO_SCHEMA))
    clean_side = _DISPOSED.format(schema=sql.Identifier(CLEAN_SCHEMA))
    moved = conn.execute(
        sql.SQL("({a} EXCEPT {b}) UNION ALL ({b} EXCEPT {a})").format(
            a=scenario_side, b=clean_side
        )
    ).fetchall()

    measured: list[tuple[int, int]] = []
    for schema in (SCENARIO_SCHEMA, CLEAN_SCHEMA):
        row = conn.execute(
            sql.SQL(
                "SELECT count(*) FILTER (WHERE result = 'reject'), count(*)"
                " FROM {}.inspection_results"
            ).format(sql.Identifier(schema))
        ).fetchone()
        assert row is not None
        measured.append((_int(row[0]), _int(row[1])))
    (hits, seen), (clean_hits, clean_seen) = measured
    assert hits > 0, (
        "no part was rejected at all, so 'the fouling moved no verdict' is a statement "
        "about an empty set"
    )
    assert not moved, (
        f"{len(moved) // 2} parts were disposed of differently with the lens fouled: "
        f"{sorted({_text(row[0]) for row in moved})[:5]} -- a fouled lens is not "
        "supposed to damage parts"
    )
    return (
        f"{hits}/{seen} rejected against the clean twin's {clean_hits}/{clean_seen}, "
        f"not one verdict moved (clarity {factor})"
    )


_CARRIES = sql.SQL(
    "EXISTS (SELECT 1 FROM unnest(r.defect_classes, r.confidences) AS scored(c, score)"
    "        WHERE scored.c = ANY(%s) AND scored.score >= %s)"
)
"""Whether one `inspection_results` row `r` carries any of the named classes.

One fragment rather than the same EXISTS written twice, because `_one_part_only` asks it
of a whole schema and of a single window and the two answers have to be the same
question. `%s` placeholders survive `sql.SQL.format`, which substitutes only `{}`.
"""

_CARRYING = sql.SQL(
    "SELECT r.assembly_serial FROM {schema}.inspection_results r WHERE {carries}"
)

_INSIDE = sql.SQL(
    "SELECT count(*), count(*) FILTER (WHERE {carries})"
    " FROM {schema}.inspection_results r WHERE r.source_ts >= %s AND r.source_ts < %s"
)


def _one_part_only(conn: Connection[tuple[object, ...]], claim: Claim) -> str:
    """Exactly one part carries the named classes over the stretch the fault could have
    reached, and it is the only part in the run that the fault changed.

    **The first half is the claim, and it is checkable on this run alone.** The fault is
    on `PRESS_CONTACT`, which S2 reads at the press, and the window it runs in is narrower
    than the fastest station's takt -- so at most one part is pressed inside it. That part
    is inspected a buffer later, which is what the consequence's `within_seconds` is: one
    `_buffer_transit_seconds`, doubled from the measured 35.1 s lag as the margin every
    derived window here carries. So the window below is eleven parts wide at the shipped
    settings, and exactly one of them is gapped. A clean run reaches that by chance at the
    baseline `CLASS_RATE_RISES` measures for the same class -- 0.2706 % over 11,457 parts,
    so 1 - (1 - 0.002706)^11, near three per cent over eleven.

    **The twin difference is the calibration and not the claim**, which is the shape the
    other paired rows have and the shape this one was missing: both halves of
    `scenario_run EXCEPT clean_run` read the clean schema, so before the window assertion
    above there was no form of this consequence that one run could support -- and a
    consequence the evidence of a single run cannot reach is exactly what row 3's `gap`
    was dropped for one commit earlier (b8a0815). What the twin adds is that the gained
    part is *that* part and that the identical window on a clean run carries none. The
    reverse difference is asserted empty too, and that half is structural rather than
    lucky -- a fault raises a threshold against a fixed draw, so a part the clean run
    marked cannot come back clean.
    """
    assert claim.until is not None and claim.within_seconds is not None, (
        "scenario 8's fault has to end and its consequence has to say how long the part "
        "may take to reach the camera, or the single-run half of this claim has no window"
    )
    window = (
        claim.at,
        claim.until + timedelta(seconds=claim.within_seconds),
    )
    inside = _INSIDE.format(schema=sql.Identifier(SCENARIO_SCHEMA), carries=_CARRIES)
    twin = _INSIDE.format(schema=sql.Identifier(CLEAN_SCHEMA), carries=_CARRIES)
    arguments = (list(claim.subjects), DEFECT_SCORE_THRESHOLD, *window)
    seen, hits = conn.execute(inside, arguments).fetchone() or (0, 0)
    clean_seen, clean_hits = conn.execute(twin, arguments).fetchone() or (0, 0)
    assert _int(seen) > 0, (
        f"no part was inspected between {window[0]} and {window[1]}, so the press that "
        "took the defective component never reached the camera and nothing here is about "
        "anything"
    )
    assert _int(hits) == 1, (
        f"{_int(hits)} of the {_int(seen)} parts inspected between {window[0]} and "
        f"{window[1]} carry {list(claim.subjects)}: one defective component is one part"
    )
    assert _int(clean_hits) == 0, (
        f"the same window on a clean run already carries {_int(clean_hits)} of "
        f"{list(claim.subjects)} over {_int(clean_seen)} parts, so 'exactly one' is "
        "answered by a line with nothing wrong with it"
    )

    scenario_side = _CARRYING.format(
        schema=sql.Identifier(SCENARIO_SCHEMA), carries=_CARRIES
    )
    clean_side = _CARRYING.format(schema=sql.Identifier(CLEAN_SCHEMA), carries=_CARRIES)
    pair = (list(claim.subjects), DEFECT_SCORE_THRESHOLD) * 2
    gained = conn.execute(
        sql.SQL("{a} EXCEPT {b}").format(a=scenario_side, b=clean_side), pair
    ).fetchall()
    lost = conn.execute(
        sql.SQL("{b} EXCEPT {a}").format(a=scenario_side, b=clean_side), pair
    ).fetchall()
    assert len(gained) == 1, (
        f"{len(gained)} parts gained {list(claim.subjects)} from one defective "
        f"component: {sorted(_text(row[0]) for row in gained)}"
    )
    assert not lost, (
        f"{len(lost)} parts stopped carrying {list(claim.subjects)}, which no fault can "
        "do to a fixed draw"
    )
    return (
        f"one of the {_int(seen)} parts in the window, {_text(gained[0][0])}, "
        f"against none of the twin's {_int(clean_seen)}"
    )


CHECKERS: dict[str, Checker] = {
    SUSPENDED_IN_ORDER: _suspended_in_order,
    BLOCKED_IN_ORDER: _blocked_in_order,
    STREAM_FALLS: _stream_falls,
    STREAM_STABLE: _stream_stable,
    ALARM_RAISED: _alarm_raised,
    STATION_ABORTS: _station_aborts,
    CLASS_RATE_RISES: _class_rate_rises,
    CLASS_MIX_SHIFTS: _class_mix_shifts,
    CLASS_CONCENTRATES: _class_concentrates,
    CONFIDENCE_DECAYS: _confidence_decays,
    SCRAP_RATE_FLAT: _scrap_rate_flat,
    ONE_PART_ONLY: _one_part_only,
}
"""One checker per expectation §3.5's eight claim.

`test_every_expectation_the_eight_scenarios_claim_has_a_checker` holds this against the
rows in both directions, so a consequence added to a row with no assertion here is a
failure rather than a claim every later milestone silently skips -- which is what
`Consequence.__post_init__` refuses an unknown *name* for, one level up.
"""


def _assert_consequences(proof: Proof) -> dict[str, str]:
    """Every consequence the log records for this run, asserted out of Postgres.

    Driven off the file rather than off the `Scenario` object, because the file is what a
    later milestone is handed. Returns each expectation's measurement, so the test that
    called it can name what it saw and so that a checker returning nothing is visible.
    """
    injections = _scenario_injections(proof.records)
    assert injections, "the log records no scenario injection at all"
    measured: dict[str, str] = {}
    for injection in injections:
        consequences = injection["consequences"]
        assert isinstance(consequences, list) and consequences, (
            f"a scenario injection of {injection['kind']} claims no consequence, so "
            "nothing about it can be asserted"
        )
        params = injection["params"]
        assert isinstance(params, dict)
        until = injection["until"]
        for item in consequences:
            claim = Claim(
                observable=str(item["observable"]),
                expect=str(item["expect"]),
                subjects=tuple(str(name) for name in item["subjects"]),
                scope=str(item["scope"]),
                within_seconds=(
                    None
                    if item["within_seconds"] is None
                    else float(item["within_seconds"])
                ),
                at=datetime.fromisoformat(str(injection["at"])),
                until=None if until is None else datetime.fromisoformat(str(until)),
                params={str(key): float(value) for key, value in params.items()},
                origin=proof.run.origin,
            )
            checker = CHECKERS.get(claim.expect)
            assert checker is not None, (
                f"the ground-truth log claims {claim.expect!r} and nothing here checks "
                "it: a consequence no assertion reaches is a silent pass"
            )
            measured[claim.expect] = checker(proof.conn, claim)
    return measured


def _within(item: Scenario, expect: str) -> float:
    within = consequence_claiming(item, expect).within_seconds
    assert within is not None, f"scenario {item.number}'s {expect} claims no window"
    return within


# --- the eight ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_1_starves_three_stations_in_buffer_order(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 1, out of `state_changes`. **The order is the claim**: a line with no
    buffers would suspend all three at one instant, and an assertion that merely counted
    them would pass on one. Measured from the injection, out of `state_changes`:
    S2 **+19.5 s**, S3 **+41.9 s**, S4 **+46.1 s**, inside the 180 s the log claims."""
    settings = Settings()
    item = scenario(1, settings)
    at, _ = fault_window(item)
    seconds = at + _within(item, SUSPENDED_IN_ORDER) + settings.takt_seconds
    measured = _assert_consequences(await _prove(database, 1, seconds, tmp_path))
    assert set(measured) == {SUSPENDED_IN_ORDER}, measured


@pytest.mark.asyncio
async def test_scenario_2_blocks_three_stations_in_buffer_order(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 2, scenario 1's mirror: the same three buffers carry the condition the
    other way, so the pair proves the buffers rather than the stations. Measured: S3
    **+30.0 s**, S2 **+37.0 s**, S1 **+49.8 s**."""
    settings = Settings()
    item = scenario(2, settings)
    at, _ = fault_window(item)
    seconds = at + _within(item, BLOCKED_IN_ORDER) + settings.takt_seconds
    measured = _assert_consequences(await _prove(database, 2, seconds, tmp_path))
    assert set(measured) == {BLOCKED_IN_ORDER}, measured


@pytest.mark.asyncio
async def test_scenario_3_drops_the_force_then_alarms_then_aborts(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 3's three checkable halves: the stream falls, `alarms` gains a row, and S2
    reaches `Aborted` after it.

    The row's `gap` half is deliberately not among them. It happens, and in the order the
    row states, but `CLASS_RATE_RISES` over one run's history is satisfied identically by
    a clean line at this magnitude -- the account is in `scenarios._joining_force_drift`,
    and this test asserting three consequences rather than four is that decision showing
    up here.
    """
    settings = Settings()
    item = scenario(3, settings)
    at, _ = fault_window(item)
    seconds = at + _within(item, ALARM_RAISED) + settings.takt_seconds
    measured = _assert_consequences(await _prove(database, 3, seconds, tmp_path))
    assert set(measured) == {STREAM_FALLS, ALARM_RAISED, STATION_ABORTS}, measured


@pytest.mark.asyncio
async def test_scenario_4_concentrates_two_classes_on_one_carrier(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 4, at the production depth, against the clean twin.

    **The depth is part of the proof and not an accident of taste.** Re-measured at three,
    over the same window this test uses: carrier 7 reaches **d=+3.70** against a clean
    run's worst at +1.23 over 12 h, **+4.05** against **+3.91** over 20 h, and **+5.92**
    against +2.57 over 33 h. The middle row is the one worth knowing -- fewer parts per
    carrier is a wider binomial spread on every carrier's count, so a clean run's
    best-looking carrier climbs towards the real one and the bar "twice the best clean
    rival" is missed outright. This runs at the depth the shipped history has.
    """
    measured = _assert_consequences(
        await _prove(database, 4, PRODUCTION_SECONDS, tmp_path, twin=True)
    )
    assert set(measured) == {CLASS_CONCENTRATES}, measured


@pytest.mark.asyncio
async def test_scenario_5_shifts_the_class_mix_towards_two_classes(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 5, as the mix rather than as a rate rise.

    **The lane is not in the answer, and that is the row's own amendment.** Every assembly
    draws one component from each lane, so every part contains lane 2 and there is no
    contrast group; §3.5 row 5 now expects "the incoming components on one feeder". What
    the database can carry is that the two classes the contamination raises outweigh the
    four it does not -- which a clean run does not do.

    **The depth is part of the proof here too.** Against `MIX_BAR`: 2.12x at 8 h, 2.39x at
    12 h, 2.95x at 20 h, 3.20x at 33 h. Eight hours clears a bar of 2.0 by six per cent,
    which is not a margin; twenty clears it by half.
    """
    measured = _assert_consequences(
        await _prove(database, 5, 20 * 3600.0, tmp_path, twin=True)
    )
    assert set(measured) == {CLASS_MIX_SHIFTS}, measured


@pytest.mark.asyncio
async def test_scenario_6_decays_all_six_scores_while_the_scrap_rate_holds(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 6, and the only one of the eight that goes through the real client.

    The verdict comes back from a stand-in classifier that reads the **frame the plant
    actually posted** and scales the six scores by its contrast, which is
    `inspection.classifier.SimulatedClassifier`'s rule and a test double of it. What that
    makes checkable is the plant's half: if the fouling stops reaching the render, the
    frames arrive clean and the scores do not fall. What it does not check is the
    classifier's own scaling -- that is the inspection package's suite, and is where it
    belongs, because the two workspaces may not import each other (§10.7).

    **The twin is here for `SCRAP_RATE_FLAT` and nothing else**, and it is what makes that
    half able to fail: "the rate did not move" measured against this run's own 300-part
    warmup sits behind a 2.727 pp bar that a tripled reject rate walks under. The clean
    run takes the cheap `truth_outcome` path -- it is the contrast group for the verdicts,
    not a second run through the classifier, and rendering a PNG per part twice would buy
    nothing `CONFIDENCE_DECAYS` does not already assert on the fouled side.
    """
    settings = Settings()
    at, _ = fault_window(scenario(6, settings))
    seconds = at + settings.optics_fouling_ramp_seconds + 3600.0
    measured = _assert_consequences(
        await _prove(
            database,
            6,
            seconds,
            tmp_path,
            twin=True,
            transport=_stand_in_classifier(),
            produce_with=lambda log: lambda client: recording(log, client),
        )
    )
    assert set(measured) == {CONFIDENCE_DECAYS, SCRAP_RATE_FLAT}, measured


@pytest.mark.asyncio
async def test_scenario_7_gaps_a_lot_while_the_force_stream_stays_put(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 7, the strongest of the eight and the easiest to ruin.

    Its symptom is row 3's symptom. **The force is the whole of what separates them**, so
    a scenario that also drifted the force would have quietly become scenario 3, and
    `STREAM_STABLE` is the assertion that says it did not: **0.059 sigma** against row 3's
    10.7, a factor of 180 on the one stream that tells the two apart.
    """
    measured = _assert_consequences(
        await _prove(database, 7, 20 * 3600.0, tmp_path, twin=True)
    )
    assert set(measured) == {CLASS_RATE_RISES, STREAM_STABLE}, measured


@pytest.mark.asyncio
async def test_scenario_8_is_one_bad_part_and_not_a_bad_lot(
    database: Connection[tuple[object, ...]], tmp_path: Path
) -> None:
    """§3.5 row 8, scenario 7's mirror, and the reason it is in the set: the same fault on
    one component instead of five hundred must read as one bad part.

    **The window being narrower than a station takt is what makes this checkable on one
    run**, and the assertion below is why that is a property of the scenario rather than
    an observation about it: at most one part can be pressed while the fault runs. It
    reaches the camera a buffer later -- measured at +2135.1 s against a window opening at
    +2100.0 -- which is what the consequence's `within_seconds` covers, and
    `_one_part_only` asserts exactly one gapped part over that stretch before it ever
    looks at the twin.
    """
    settings = Settings()
    at, until = fault_window(scenario(8, settings))
    assert until is not None and until - at < min(
        settings.station_takt_seconds.values()
    ), "the window is wider than a station takt, so more than one part could be pressed"
    measured = _assert_consequences(
        await _prove(database, 8, at + 900.0, tmp_path, twin=True)
    )
    assert set(measured) == {ONE_PART_ONLY}, measured


# --- what stops the eight above from being vacuous -----------------------------------------


def test_every_expectation_the_eight_scenarios_claim_has_a_checker() -> None:
    """Both directions, and the first is the one that matters.

    A consequence added to a row with no checker here is written to the ground-truth log,
    skipped by the dispatcher and read as a scenario whose claims all held -- the silent
    pass `Consequence.__post_init__` refuses an unknown *name* for, one level up and with
    no equivalent at this level until this test. The reverse direction catches a checker
    kept alive for a claim no row makes any more.
    """
    claimed = {
        consequence.expect
        for item in all_scenarios(Settings())
        for injection in item.injections
        for consequence in injection.consequences
    }
    assert claimed <= set(CHECKERS), (
        f"§3.5's rows claim {sorted(claimed - set(CHECKERS))} and nothing asserts it"
    )
    assert set(CHECKERS) <= claimed, (
        f"{sorted(set(CHECKERS) - claimed)} is checked here and claimed by no row"
    )


def test_only_a_scenarios_injection_carries_a_claim_to_assert(tmp_path: Path) -> None:
    """The `source` selection in `_scenario_injections`, made to fail rather than trusted.

    §3.7's panel writes an operator's injection with `"consequences": []`, because nobody
    wrote down what should follow from a fault chosen at a keyboard. A dispatcher that
    selected on "has consequences" would read that as a scenario that claimed nothing and
    score it satisfied; one that ignored `source` would demand consequences of it and fail
    a run somebody used the panel on.

    The two values are asserted as **literals** and not against the module's own
    constants, because `source` is a wire format a later milestone reads out of a file: a
    check that moves when the thing it checks moves proves nothing, and this repository
    has already shipped that mistake once on this exact field.
    """
    settings = Settings()
    item = scenario(1, settings)
    clock = new_clock(settings)
    path = tmp_path / "mixed.jsonl"
    with open_log(path, settings, clock, item) as log:
        Injector(item.fault_set(clock.history_start), log, clock.history_start).inject(
            FaultKind.OPTICS_FOULING,
            {"factor": 0.7},
            clock.history_start + timedelta(seconds=60),
        )

    records = tuple(
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    )
    every = [record for record in records if record["record"] == "injection"]
    assert [record["source"] for record in every] == ["scenario", "operator"]
    assert [record["consequences"] for record in every][1] == []

    selected = _scenario_injections(records)
    assert [record["source"] for record in selected] == ["scenario"]
    assert all(record["consequences"] for record in selected)


# --- scenario 6's stand-in classifier -------------------------------------------------------


_REFERENCE_CONTRAST = statistics.fmean(
    rms_contrast(
        render_part(
            f"A-{index:08d}",
            [],
            Settings().image_width,
            Settings().image_height,
            Settings().seed,
            Settings().image_compress_level,
            1.0,
        )
    )
    for index in range(20)
)
"""What a clean frame carries, measured here rather than configured.

`inspection.classifier` holds the same number as `reference_contrast` and the two
workspaces may not import each other, so a literal here would be a third copy free to
drift from both. Measured at the shipped settings: **66.36** grey levels of RMS contrast.
"""


def _stand_in_classifier() -> httpx.MockTransport:
    """The inspection service, as far as `InspectionClient.produce` can tell.

    **A test double and not a second classifier.** It does exactly two things: it keeps
    the truth the client declared on §4.5's side channel, so the verdict is the plant's
    own rather than a model's two error rates (D7); and it scales all six scores by the
    contrast of the frame the client **actually posted**, which is `SimulatedClassifier`'s
    own rule. The scaling is what makes scenario 6's proof a proof about the plant: delete
    the `clarity=` argument from `produce` and the frames arrive clean, the scores do not
    fall, and `CONFIDENCE_DECAYS` fails.
    """
    truth: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/truth/"):
            declared = json.loads(request.content)["defects"]
            truth[request.url.path.rsplit("/", 1)[-1]] = [
                str(name) for name in declared
            ]
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content)
        found = truth[str(body["part_id"])]
        clarity = (
            rms_contrast(base64.b64decode(str(body["image_b64"]))) / _REFERENCE_CONTRAST
        )
        return httpx.Response(
            200,
            json={
                "disposition": "reject" if found else "good",
                "defect_class": found[0] if found else None,
                "confidence": 0.93 * clarity,
                "confidences": {
                    name: (0.82 if name in found else 0.045) * clarity
                    for name in DEFECT_CLASSES
                },
                "model_version": "test-1",
            },
        )

    return httpx.MockTransport(handler)
