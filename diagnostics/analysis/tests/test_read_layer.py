"""The grant boundary, asserted rather than asserted *about*.

CLAUDE.md states as an invariant that "the analysis service reads views and cannot write;
the database enforces it, not convention", and `analysis.db`'s docstring said the same. Both
were false until M3 Task 1: every table sat flat in `public` and both services connected as
the same superuser. These tests are what makes the sentence true — each one fails if the
enforcement is dropped, which no amount of reading the code can tell you.

The role is exercised through a real connection as `analysis`, never through the migration
runner's, because a boundary tested from the privileged side of itself is not tested.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors, sql

READ_VIEWS = (
    "alarms",
    "assemblies",
    "buffer_levels",
    "buffers",
    "carriers",
    "component_lots",
    "components",
    "genealogy",
    "ingest_gaps",
    "inspection_images",
    "inspection_results",
    "part_dispositions",
    "part_process_curves",
    "part_process_values",
    "signals",
    "state_changes_settled",
    "stations",
)
"""The read contract, written down so widening it is a deliberate act.

Listed rather than discovered, unlike `conftest.MIGRATIONS`: a set read from the catalogue
would agree with whatever the migration happens to say, and the whole value of a contract is
that one side of it cannot move alone.
"""

WITHHELD = ("raw_events", "backfill_windows", "part_station_events")
"""§5.2 tables the read layer deliberately does not expose, each for its own reason.

`raw_events` and `backfill_windows` are the gateway's own bookkeeping — the verbatim arrival
log and R1's reconciliation ledger — and an analysis reaching into them would be answering
from what the gateway did rather than from what the plant produced. `part_station_events`
has no source at all: no event §4.1 publishes carries a station entry or exit instant, so
every row it could return would be an inference.
"""


def _select_one(schema: str, relation: str) -> sql.Composed:
    return sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(schema, relation))


@pytest.mark.parametrize("view", READ_VIEWS)
def test_the_analysis_role_can_read_every_view_in_the_contract(
    analysis_url: str, view: str
) -> None:
    with psycopg.connect(analysis_url) as conn:
        conn.execute(_select_one("read", view)).fetchall()


def test_the_contract_is_exactly_the_views_the_migration_creates(
    analysis_url: str,
) -> None:
    """A view in the schema and not in `READ_VIEWS` is a widening nobody reviewed."""
    with psycopg.connect(analysis_url) as conn:
        published = {
            str(row[0])
            for row in conn.execute(
                "SELECT table_name FROM information_schema.views"
                " WHERE table_schema = 'read'"
            ).fetchall()
        }

    assert published == set(READ_VIEWS)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ingest.stations (code, name) VALUES ('S9', 'Forged')",
        "UPDATE ingest.stations SET name = 'Forged'",
        "DELETE FROM ingest.stations",
    ],
)
def test_the_analysis_role_cannot_write_an_ingest_table(
    analysis_url: str, statement: str
) -> None:
    """The specific exception, not "some error".

    A connection failure, a missing table or a syntax error would all satisfy
    `pytest.raises(Exception)` while proving nothing about privileges — and the failure
    this stands against is a write that quietly succeeds.
    """
    with (
        psycopg.connect(analysis_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(statement)


def test_the_analysis_role_cannot_write_through_a_read_view(analysis_url: str) -> None:
    """A single-table view with no aggregation is *auto-updatable* in PostgreSQL.

    So `read.stations` would accept an INSERT and pass it down to `ingest.stations` if the
    role held INSERT on it. Only SELECT is granted, which is what closes the one write path
    the schema boundary on its own does not.
    """
    with (
        psycopg.connect(analysis_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("INSERT INTO read.stations (code, name) VALUES ('S9', 'Forged')")


@pytest.mark.parametrize("table", READ_VIEWS)
def test_the_analysis_role_cannot_reach_an_ingest_table_directly(
    analysis_url: str, table: str
) -> None:
    """The view is the contract, so the table behind it has to be unreachable.

    Without this the read layer would be a naming convention: the gateway could not
    restructure underneath a view anyone was free to bypass.
    """
    with (
        psycopg.connect(analysis_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(_select_one("ingest", table))


@pytest.mark.parametrize("table", WITHHELD)
def test_a_table_the_read_layer_withholds_is_unreachable(
    analysis_url: str, table: str
) -> None:
    with (
        psycopg.connect(analysis_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(_select_one("ingest", table))


def test_a_view_added_after_the_migration_is_readable_without_a_further_grant(
    postgres: str, analysis_url: str
) -> None:
    """The `ALTER DEFAULT PRIVILEGES` trap, closed and kept closed.

    Default privileges apply only to objects created by the role they *name*, and
    PostgreSQL does not inherit them through role membership (docs/ENGINEERING.md §8). Name
    the wrong role and every view added after this migration silently misses its grant —
    nothing fails at migration time, nothing fails at deploy time, and the first symptom is
    a reader hitting a view that was added months earlier.

    So: create a table and a view the way a later migration would, as the role the
    migration runner actually connects as, and read it as `analysis` with no grant in
    between. The runner's name differs between environments on purpose — `postgres` under
    Compose, `test` under testcontainers — which is why the migration cannot hardcode one.
    """
    with psycopg.connect(postgres) as runner:
        runner.execute("CREATE TABLE ingest.later_migration (id integer PRIMARY KEY)")
        runner.execute("INSERT INTO ingest.later_migration (id) VALUES (1)")
        runner.execute(
            "CREATE VIEW read.later_migration AS SELECT id FROM ingest.later_migration"
        )
        runner.commit()

    try:
        with psycopg.connect(analysis_url) as reader:
            rows = reader.execute("SELECT id FROM read.later_migration").fetchall()
        assert rows == [(1,)]
    finally:
        # Not an exception handler: the container is session-scoped, and a view left behind
        # would fail `test_the_contract_is_exactly_the_views_the_migration_creates` for a
        # reason that belongs to this test rather than to that one.
        with psycopg.connect(postgres) as runner:
            runner.execute("DROP VIEW read.later_migration")
            runner.execute("DROP TABLE ingest.later_migration")
            runner.commit()
