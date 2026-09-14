"""§1's traceability proof: a serial answers for its whole life, and nothing infers it.

§14 asks that any serial be traceable end to end. §3.4a says how: the per-part record is
written at the instant of production and is never reconstructed by joining the time series
on "which part was at S2 at 02:14:07". The difference is invisible in a passing response —
both readings return a number — so these tests are built so that the reconstruction returns
a *different* number, and one of them watches what the database was actually asked.

Fast, in process against the same container the other tests use, and therefore in `make
check` rather than behind `make verify`. Same reasoning as the plant's propagation proof:
the proofs `make verify` holds back are the ones that stop and restart containers, and a
proof cheap enough for the gate belongs in the gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg
import pytest
from analysis.db import reset_pool
from fastapi.testclient import TestClient
from psycopg import sql
from testcontainers.postgres import PostgresContainer

from tests.conftest import (
    DECOY_DISTANCE,
    DECOY_FORCE,
    curve_for,
    joining_distance,
    peak_force,
)

TRACED = "A-00000123"
"""One ordinary part, mid-window, with every section of its history filled in."""


def _value(body: dict[str, object], signal: str) -> float:
    """The one per-part number recorded under `signal`, or a failure naming what was there."""
    rows = body["process_values"]
    assert isinstance(rows, list)
    matching = [row for row in rows if row["signal"] == signal]
    assert len(matching) == 1, f"expected one {signal}, got {rows}"
    return float(matching[0]["value"])


@pytest.mark.usefixtures("seeded_db")
def test_the_press_record_is_the_parts_own_and_not_the_time_series(
    client: TestClient, seeded_db: str
) -> None:
    """§3.4a's rule, made falsifiable.

    S2's historised streams are seeded across the whole window at values three orders of
    magnitude away from anything a part carries, so a read path that reached for "what was
    S2 publishing when this part went through" would answer 900-something instead of
    100-something. The assertion below is on the part's own number, and the one before it
    is what stops the proof passing because there was nothing to reconstruct from.
    """
    with psycopg.connect(seeded_db) as conn:
        around = conn.execute(
            "SELECT count(*) FROM signals s JOIN stations st ON st.id = s.station_id"
            " WHERE st.code = 'S2' AND s.signal IN ('JoiningForcePeak', 'PeakForce')"
        ).fetchone()
    assert around is not None and around[0] > 0, (
        "the decoy time series is empty, so this proof would pass against a read path "
        "that joined on time and found nothing"
    )

    body = client.get(f"/parts/{TRACED}").json()

    assert _value(body, "PeakForce") == peak_force(123)
    assert _value(body, "JoiningDistance") == joining_distance(123)
    assert _value(body, "PeakForce") < DECOY_FORCE
    assert _value(body, "JoiningDistance") < DECOY_DISTANCE


@pytest.mark.usefixtures("seeded_db")
def test_the_curve_is_the_one_the_press_recorded_for_this_serial(
    client: TestClient,
) -> None:
    """D6's curve is the section no reconstruction can produce at all: the historised
    streams carry the two scalars and nothing that a stroke could be rebuilt from. A trace
    that quietly dropped it would still look complete."""
    curves = client.get(f"/parts/{TRACED}").json()["process_curves"]

    assert [curve["signal"] for curve in curves] == ["Curve"]
    assert curves[0]["samples"] == curve_for(123)


@pytest.mark.usefixtures("seeded_db")
def test_two_parts_made_at_the_same_instant_keep_their_own_histories(
    client: TestClient,
) -> None:
    """The case an inference cannot get right.

    Both assemblies were created at `TWIN_INSTANT`, so "which part was on the line then"
    has two correct answers and any reconstruction keyed on time must give at least one of
    them the other's components and the other's press record. On the real line the same
    ambiguity arrives through buffers, jitter and history gaps rather than through an exact
    tie; §3.4a's point is that it arrives.
    """
    first = client.get("/parts/A-TWIN-1").json()
    second = client.get("/parts/A-TWIN-2").json()

    assert first["created_at"] == second["created_at"]
    assert [row["component_serial"] for row in first["genealogy"]] == [
        "C1-A-TWIN-1",
        "C2-A-TWIN-1",
    ]
    assert [row["component_serial"] for row in second["genealogy"]] == [
        "C1-A-TWIN-2",
        "C2-A-TWIN-2",
    ]
    assert _value(first, "PeakForce") != _value(second, "PeakForce")


@pytest.mark.usefixtures("seeded_db")
def test_a_part_still_on_the_line_is_answered_rather_than_omitted(
    client: TestClient,
) -> None:
    """Every backfill's upper bound falls with parts mid-line, so "pressed, not yet
    inspected" is the ordinary state of several serials at any instant. Omitting them
    would shorten a containment list without saying so."""
    body = client.get("/parts/A-TWIN-1").json()

    assert body["inspection"] is None
    assert body["disposition"] is None
    assert body["genealogy"] != []
    assert body["process_curves"] != []


@pytest.mark.usefixtures("seeded_db")
def test_a_part_from_before_the_horizon_says_unknown_rather_than_going_missing(
    client: TestClient,
) -> None:
    """On every boot the parts already in the buffers were created before the window the
    backfill starts at, so their `AssemblyCreatedEvent` never arrives — no creation
    instant, no carrier, no genealogy. The part is real and partially known, and the whole
    value of the trace is that it says which half it has."""
    response = client.get("/parts/A-HORIZON")

    assert response.status_code == 200
    body = response.json()
    assert body["created_at"] is None
    assert body["carrier_id"] is None
    assert body["genealogy"] == []
    # What *is* known is answered in full, which is what distinguishes "unknown" from
    # "nothing here".
    assert body["inspection"]["result"] == "good"
    assert body["process_curves"] != []


@pytest.mark.usefixtures("seeded_db")
def test_a_component_with_no_read_event_stays_in_its_assembly(
    client: TestClient,
) -> None:
    """The other half of the horizon, and the one that decides whether a containment list
    is usable: a component drawn before the horizon has no lot, and an inner join to
    `component_lots` would drop it from the assembly it is part of — leaving an assembly
    that looks single-component and a lot recall that misses it."""
    genealogy = client.get("/parts/A-STUBLOT").json()["genealogy"]

    assert [row["component_serial"] for row in genealogy] == ["C1-STUB", "C2-STUBLOT"]
    assert genealogy[0]["lot_code"] is None
    assert genealogy[0]["lane"] is None
    assert genealogy[0]["read_at"] is None
    assert genealogy[1]["lot_code"] == "LOT-B1"


# --- what the database was actually asked ---------------------------------------------

_PREFIXED = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \w+ \[\d+\] (\w+):\s+(.*)$"
)
_TIME_RANGE = re.compile(
    r"\b(source_ts|created_at|read_at|loaded_at|received_at|server_ts|entered_at|left_at"
    r"|\bat\b)\s*(>=|<=|>|<|between)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LoggedStatement:
    """One statement Postgres reports having executed, with the parameters it bound."""

    sql: str
    parameters: str


def _statements(log: str) -> list[LoggedStatement]:
    """Parse `log_statement = all` output into the statements it records.

    Reads Postgres's own log rather than instrumenting the service, because what is being
    proved is what the database was asked — not what the Python around it meant to ask.
    """
    found: list[list[str]] = []
    parameters: list[str] = []
    for line in log.splitlines():
        prefixed = _PREFIXED.match(line)
        if prefixed is None:
            # A statement spanning several lines; the continuation belongs to the last one.
            if found:
                found[-1].append(line.strip())
            continue
        level, text = prefixed.groups()
        if level == "LOG" and text.startswith(("execute ", "statement: ")):
            found.append([text.split(": ", 1)[1]])
            parameters.append("")
        elif level == "DETAIL" and text.startswith("Parameters:") and parameters:
            parameters[-1] = text
    return [
        LoggedStatement(" ".join(sql), bound)
        for sql, bound in zip(found, parameters, strict=True)
    ]


@pytest.mark.usefixtures("seeded_db")
def test_the_trace_asks_the_database_for_one_serial_and_never_for_a_time_range(
    client: TestClient, postgres_container: PostgresContainer, seeded_db: str
) -> None:
    """The proof §1 asks for, stated as what the server received.

    Every statement the trace issues against a per-part table names the serial among its
    bound parameters, and not one of them compares a time column against anything. A read
    path that reconstructed the association would have to bind a window instead — that is
    what a time-range join *is* — so this fails the moment one appears, including in a
    section added later that nobody thought to write a value assertion for.
    """
    with psycopg.connect(seeded_db) as conn:
        # Database-wide rather than per session, and applied by name rather than to
        # `current_database()`: the service's pool opens a connection of its own, and a SET
        # on this one would never reach it.
        database = sql.Identifier(conn.info.dbname)
        conn.execute(
            sql.SQL("ALTER DATABASE {} SET log_statement = 'all'").format(database)
        )
        conn.commit()
    # So the pooled connection is opened after the setting above, and picks it up.
    reset_pool()

    before = len(postgres_container.get_logs()[1])
    try:
        assert client.get(f"/parts/{TRACED}").status_code == 200
    finally:
        # Not an exception handler: logging every statement is a setting on a session-scoped
        # container, and leaving it on would bury every later test's output in this one's.
        with psycopg.connect(seeded_db) as conn:
            conn.execute(
                sql.SQL("ALTER DATABASE {} RESET log_statement").format(database)
            )
            conn.commit()

    issued = _statements(postgres_container.get_logs()[1][before:].decode())
    tables = (
        "assemblies",
        "genealogy",
        "part_process_values",
        "part_process_curves",
        "inspection_results",
        "part_dispositions",
    )
    traced = [
        statement
        for statement in issued
        if any(table in statement.sql for table in tables)
    ]

    # First, because every assertion after it is vacuous over an empty list — and a proof
    # that passes by having observed nothing is the failure mode this milestone keeps
    # finding. Six: one per table above, which is also one per section of §14's line.
    assert len(traced) == len(tables), (
        f"expected one statement per per-part table, saw {[s.sql for s in traced]}"
    )

    for statement in traced:
        assert TRACED in statement.parameters, (
            f"a statement in the trace is not keyed by the serial: {statement.sql}"
        )
        assert _TIME_RANGE.search(statement.sql) is None, (
            f"the trace compared a time column: {statement.sql}"
        )


def test_the_statement_parser_finds_what_it_is_asked_to_find() -> None:
    """The parser above is the whole instrument of the proof beside it, and a parser that
    silently matched nothing would make that proof pass by observing nothing. The count
    assertion there catches that; this says what the shapes are."""
    parsed = _statements(
        "2026-09-14 19:09:19.020 UTC [84] LOG:  statement: BEGIN\n"
        "2026-09-14 19:09:19.020 UTC [84] LOG:  execute <unnamed>: SELECT created_at\n"
        "\tFROM assemblies WHERE serial = $1\n"
        "2026-09-14 19:09:19.020 UTC [84] DETAIL:  Parameters: $1 = 'A-00000123'\n"
    )

    assert [statement.sql for statement in parsed] == [
        "BEGIN",
        "SELECT created_at FROM assemblies WHERE serial = $1",
    ]
    assert parsed[1].parameters == "Parameters: $1 = 'A-00000123'"
    assert _TIME_RANGE.search(parsed[1].sql) is None
    # The shape the proof exists to catch: /inspection/stats issues exactly this, which is
    # why a pattern that matched nothing would leave the proof beside it asserting nothing.
    assert _TIME_RANGE.search("WHERE source_ts >= $1 AND source_ts < $2") is not None
