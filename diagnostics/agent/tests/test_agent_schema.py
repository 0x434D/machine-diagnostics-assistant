"""The agent's ownership boundary, and the trap migration 005 left for whoever crossed it.

Three claims, none of which reading the code can settle:

  1. The `agent` role writes `agent.*` and nothing else — it cannot read an `ingest` table
     and cannot write through a `read` view. The specific `InsufficientPrivilege`, never
     "some error", for the reason `analysis/tests/test_read_layer.py` gives: a connection
     failure, a missing table or a syntax error would all satisfy `pytest.raises(Exception)`
     while proving nothing about privileges.
  2. An unqualified `CREATE TABLE` from an Alembic migration lands in `agent`. 005 set
     `search_path = ingest, public` on the *database*, which applies to every role that
     connects, and its own comment says what that costs whoever adds a second owner: agent
     tables in the gateway's schema, owned by the wrong role, truncated by the gateway's
     fixtures. The mechanism that closes it is `ALTER ROLE agent SET search_path = agent`
     in the migration that creates the role — a role-level setting overrides a
     database-level one — and it bites only because Alembic connects *as* `agent`.
  3. Nothing the agent writes needs `ingest` or `read`, so the role holds no rights there
     and a migration whose search_path was wrong would fail rather than land somewhere
     quiet. Belt and braces: (2) puts the table in the right schema, (3) makes the wrong
     one unreachable.

Real Postgres, both migration sets, and every assertion made through a connection
authenticated as `agent` — a boundary tested from the privileged side of itself is not
tested.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from agent.config import Settings
from agent.sessions import remember
from alembic import command
from alembic.config import Config
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict
from testcontainers.postgres import PostgresContainer

AGENT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = AGENT / "alembic.ini"

GATEWAY_MIGRATIONS = AGENT.parents[0] / "gateway" / "Gateway" / "Migrations"
"""The gateway's embedded SQL, read from the directory rather than listed.

The agent's schema shares an instance with it, and the database-level `search_path` that
makes this file necessary is set by 005. Applying them is not fixture scenery: without
`ingest` and `read` in place there is nothing for the agent role to be refused.
"""

# Pinned by digest, not tag (§10.7). scripts/pin-images.sh re-resolves it.
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)

BOOTSTRAP_REVISION = "0001"
"""The revision that creates the role, and therefore the one the role cannot apply.

Everything after it runs as `agent`. That split is the deployment's, not this fixture's:
`ALTER ROLE agent SET search_path = agent` is a property of `agent` sessions, so a
migration runner that stayed the owner forever would leave the trap open for migration 002.
"""

AGENT_PASSWORD = "agent-under-test"
"""What the agent role authenticates with here, and nowhere else.

001 creates the role able to log in and with no password, the way 005 creates `analysis`:
a credential in a migration is a credential in the repository, and a deployment that never
provisions one should be unreachable rather than reachable by whoever guesses first.
Whoever owns the database sets it — Compose through the migration step, this fixture for
the container it just started.
"""

INGEST_TABLES = ("stations", "signals", "inspection_results", "raw_events", "alarms")
READ_VIEWS = ("stations", "inspection_results")

PROBE_REVISION = '''"""A later migration, written the way a later migration will be written.

Unqualified on purpose: this is the statement 005's warning is about.
"""

from alembic import op

revision = "probe"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE trap_probe (id integer PRIMARY KEY)")


def downgrade() -> None:
    op.execute("DROP TABLE trap_probe")
'''


def probe_versions(tmp_path: Path) -> str:
    """A second version directory holding one revision, and the path Alembic reads both from.

    Written here rather than committed so that what it contains is visible in the test that
    depends on it: `CREATE TABLE` with no schema on the name, which is the statement 005's
    warning is about.
    """
    probe = tmp_path / "versions"
    probe.mkdir()
    (probe / "probe_unqualified_create_table.py").write_text(PROBE_REVISION)
    return os.pathsep.join([str(AGENT / "alembic" / "versions"), str(probe)])


def as_role(url: str, user: str, password: str) -> str:
    """The same database, reached as another role.

    A URI rather than psycopg's keyword/value form, because Alembic's engine and psycopg
    both read this string and only one of them speaks both.
    """
    parts = conninfo_to_dict(url)
    return (
        f"postgresql://{user}:{password}"
        f"@{parts['host']}:{parts['port']}/{parts['dbname']}"
    )


def alembic_config(*, version_locations: str | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    if version_locations is not None:
        config.set_main_option("version_locations", version_locations)
    return config


def upgrade(
    url: str,
    revision: str,
    *,
    version_locations: str | None = None,
    role_password: str | None = None,
) -> None:
    """One migration run, as whoever `url` authenticates as.

    Through AGENT_DATABASE_URL rather than through Alembic's own config, because that is
    the one place the deployment states it: Compose's bootstrap hook picks the owner for
    its own process by setting this same variable.
    """
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AGENT_DATABASE_URL", url)
        if role_password is not None:
            environment.setenv("AGENT_ROLE_PASSWORD", role_password)
        command.upgrade(alembic_config(version_locations=version_locations), revision)


def downgrade(url: str, revision: str, *, version_locations: str | None = None) -> None:
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AGENT_DATABASE_URL", url)
        command.downgrade(alembic_config(version_locations=version_locations), revision)


@pytest.fixture(scope="module")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        yield container


@pytest.fixture(scope="module")
def owner_url(postgres_container: PostgresContainer) -> str:
    """The database as the migration runner reaches it, with the gateway's schema applied."""
    # str(), because testcontainers ships no py.typed and mypy cannot take its word for the
    # return type -- see the [mypy-testcontainers.*] override.
    url = str(postgres_container.get_connection_url())
    with psycopg.connect(url) as conn:
        for migration in sorted(GATEWAY_MIGRATIONS.glob("*.sql")):
            conn.execute(migration.read_text())
        conn.commit()
    return url


@pytest.fixture(scope="module")
def agent_url(owner_url: str) -> str:
    """Both migration sets applied, returned as the role the service deploys with.

    Two identities, because the role cannot create itself: the owner applies the bootstrap
    revision and hands the role its password, and `agent` applies everything after it. The
    gateway does the same two things in the same order at boot for `analysis` (Program.cs:
    `ApplySchemaAsync`, then `SetAnalysisRolePasswordAsync`).

    The password arrives through the environment the deployment sets rather than through an
    `ALTER ROLE` written here, so what this fixture exercises is the path Compose takes. A
    fixture that provisioned the role itself would leave that path run by nothing.
    """
    upgrade(owner_url, BOOTSTRAP_REVISION, role_password=AGENT_PASSWORD)
    url = as_role(owner_url, "agent", AGENT_PASSWORD)
    upgrade(url, "head")
    return url


def test_the_agent_role_writes_its_own_tables(agent_url: str) -> None:
    """One session, one message, its trace and its feedback — §5.2's four tables in order.

    Written through the foreign keys rather than into four unrelated tables, because what
    §5.2 buys is an audit trail: a trace that belongs to no message is not one.
    """
    now = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
    with psycopg.connect(agent_url) as conn:
        session_id = conn.execute(
            "INSERT INTO agent.sessions (subject, created_at) VALUES (%s, %s)"
            " RETURNING id",
            ("auth0|operator-7", now),
        ).fetchone()
        assert session_id is not None
        conn.execute(
            "INSERT INTO agent.messages (session_id, seq, role, content, created_at)"
            " VALUES (%s, 1, 'user', %s, %s)",
            (session_id[0], "why did the line stop at 02:14?", now),
        )
        conn.execute(
            "INSERT INTO agent.traces"
            " (session_id, message_seq, sops_loaded, tool_calls, budget, timings)"
            " VALUES (%s, 1, %s, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb)",
            (session_id[0], ["CORE-01", "SOP-01"]),
        )
        conn.execute(
            "INSERT INTO agent.feedback"
            " (session_id, message_seq, useful, matched_reality, comment, created_at)"
            " VALUES (%s, 1, true, false, %s, %s)",
            (session_id[0], "the carrier was the cause, not the press", now),
        )
        conn.commit()

        stored = conn.execute(
            "SELECT m.content, t.sops_loaded, f.matched_reality"
            " FROM agent.messages m"
            " JOIN agent.traces t ON (t.session_id, t.message_seq) = (m.session_id, m.seq)"
            " JOIN agent.feedback f ON (f.session_id, f.message_seq) = (m.session_id, m.seq)"
            " WHERE m.session_id = %s",
            (session_id[0],),
        ).fetchall()

    assert stored == [("why did the line stop at 02:14?", ["CORE-01", "SOP-01"], False)]


async def test_a_session_carries_the_subject_off_the_token(agent_url: str) -> None:
    """§5.2's `subject` is the OIDC `sub`, and M5 is where it starts arriving.

    Through `sessions.remember` rather than through an INSERT written here: what M5 claims
    is that the `sub` on the token reaches this column, and an INSERT in a test proves only
    that the column accepts text. This is the function `POST /ask` calls.
    """
    session = await remember(
        "operator-7",
        datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        Settings(database_url=agent_url),
    )

    with psycopg.connect(agent_url) as conn:
        row = conn.execute(
            "SELECT subject FROM agent.sessions WHERE id = %s", (session,)
        ).fetchone()

    assert row == ("operator-7",)


async def test_continuing_a_session_does_not_rewrite_whose_it_is(
    agent_url: str,
) -> None:
    """A session belongs to whoever opened it. Without this, a second question naming an
    existing id would quietly re-attribute every message already hanging off it."""
    settings = Settings(database_url=agent_url)
    opened = await remember(
        "operator-7", datetime(2026, 9, 16, 9, 0, tzinfo=UTC), settings
    )

    continued = await remember(
        "someone-else", datetime(2026, 9, 16, 9, 5, tzinfo=UTC), settings, opened
    )

    assert continued == opened
    with psycopg.connect(agent_url) as conn:
        row = conn.execute(
            "SELECT subject FROM agent.sessions WHERE id = %s", (opened,)
        ).fetchone()
    assert row == ("operator-7",)


def test_session_ids_are_not_guessable(agent_url: str) -> None:
    """§6.10 requires it, and a session id is what stands in for a login until M5."""
    with psycopg.connect(agent_url) as conn:
        first = conn.execute(
            "INSERT INTO agent.sessions (created_at) VALUES (now()) RETURNING id"
        ).fetchone()
        second = conn.execute(
            "INSERT INTO agent.sessions (created_at) VALUES (now()) RETURNING id"
        ).fetchone()
        conn.commit()

    assert first is not None
    assert second is not None
    assert first[0] != second[0]
    assert uuid.UUID(str(first[0])).version == 4
    assert uuid.UUID(str(second[0])).version == 4


@pytest.mark.parametrize("table", INGEST_TABLES)
def test_the_agent_role_cannot_read_an_ingest_table(agent_url: str, table: str) -> None:
    """The gateway's tables are the gateway's. The agent answers from the analysis service.

    Which is §2.2's separation stated as a permission: an agent that could read `ingest`
    directly would be a second, undeclared reader of the plant's telemetry, joining around
    the read contract that exists so the gateway can restructure underneath it.
    """
    with (
        psycopg.connect(agent_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(
            sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier("ingest", table))
        )


@pytest.mark.parametrize("view", READ_VIEWS)
def test_the_agent_role_cannot_write_through_a_read_view(
    agent_url: str, view: str
) -> None:
    """`read` is the analysis service's contract and is read-only even for its owner's peers.

    A single-table view with no aggregation is auto-updatable in PostgreSQL, so this is a
    real write path rather than a hypothetical one.
    """
    with (
        psycopg.connect(agent_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier("read", view)))


def test_the_agent_role_cannot_create_a_table_in_the_gateways_schema(
    agent_url: str,
) -> None:
    """The second half of the trap, closed by grants rather than by search_path.

    `ALTER ROLE agent SET search_path = agent` decides where an unqualified name lands;
    this decides what happens to a qualified one that names `ingest` anyway — whether by a
    migration someone writes by hand or by a search_path that was got wrong. A role with no
    rights in `ingest` cannot put a table there under any spelling.
    """
    with (
        psycopg.connect(agent_url) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("CREATE TABLE ingest.forged (id integer PRIMARY KEY)")


def test_the_four_tables_are_owned_by_the_agent_role(agent_url: str) -> None:
    """005's symptom, named: "agent tables owned by the wrong role".

    Ownership is what the gateway's fixtures and a later `DROP OWNED BY` see. A table in
    `agent` owned by the migration runner would read as the agent's and belong to whoever
    happened to run the migration.
    """
    with psycopg.connect(agent_url) as conn:
        owners = conn.execute(
            "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'agent'"
            " ORDER BY tablename"
        ).fetchall()

    assert owners == [
        ("alembic_version", "agent"),
        ("feedback", "agent"),
        ("messages", "agent"),
        ("sessions", "agent"),
        ("traces", "agent"),
    ]


def test_an_unqualified_create_table_from_a_migration_lands_in_the_agent_schema(
    agent_url: str, tmp_path: Path
) -> None:
    """**The trap, closed.** This is the test 005's warning was written for.

    The database-level `search_path` is `ingest, public` and applies to every role that
    connects. A migration written the way migrations are written — `CREATE TABLE` with no
    schema on it — would therefore land in the gateway's schema, and nothing about that
    fails: the migration succeeds, the table exists, the agent reads and writes it, and the
    first symptom is the gateway's fixtures truncating it.

    Remove `ALTER ROLE agent SET search_path = agent` from 001 and this test is what fails.
    """
    locations = probe_versions(tmp_path)

    upgrade(agent_url, "probe", version_locations=locations)
    try:
        with psycopg.connect(agent_url) as conn:
            landed = conn.execute(
                "SELECT schemaname, tableowner FROM pg_tables"
                " WHERE tablename = 'trap_probe'"
            ).fetchall()
    finally:
        # Not an exception handler: the container is module-scoped and a table left behind
        # would fail `test_the_four_tables_are_owned_by_the_agent_role` for a reason that
        # belongs to this test rather than to that one.
        downgrade(agent_url, BOOTSTRAP_REVISION, version_locations=locations)

    assert landed == [("agent", "agent")]


def test_a_migration_run_with_the_owners_credentials_still_lands_in_the_agent_schema(
    agent_url: str, owner_url: str, tmp_path: Path
) -> None:
    """The half a role-level setting cannot reach, and the one review found open.

    `ALTER ROLE agent SET search_path = agent` defends sessions of that role. The bootstrap
    is two commands that differ in one environment variable, and the owner's is the first of
    them — so an operator retrying a failed migration with the credential they still have in
    their shell, or a CI job that only holds the owner's, runs every revision with the
    database's own `ingest, public` and puts the agent's next table in the gateway's schema.
    Measured before the fix: `LANDED-AS-OWNER: [('ingest', 'test')]`, migration reporting
    success.

    `env.py` now pins the search path on the connection, which outranks both defaults, so the
    landing schema is a property of the migration environment rather than of who was handed
    which credential. This is the test that says so: the same revision, the same unqualified
    statement, the owner's connection.

    It does not land *owned* by `agent` — that is the revision's job (0001's
    `ALTER TABLE ... OWNER TO agent`, and the template says so), and the failure when a
    revision forgets is the service being refused at its first write. Loud, unlike this one.
    """
    locations = probe_versions(tmp_path)

    upgrade(owner_url, "probe", version_locations=locations)
    try:
        with psycopg.connect(agent_url) as conn:
            landed = conn.execute(
                "SELECT schemaname FROM pg_tables WHERE tablename = 'trap_probe'"
            ).fetchall()
    finally:
        downgrade(owner_url, BOOTSTRAP_REVISION, version_locations=locations)

    assert landed == [("agent",)]


def test_an_unqualified_statement_from_the_service_lands_in_the_agent_schema(
    agent_url: str,
) -> None:
    """What the `ALTER ROLE` in 0001 is for, now that migrations no longer rest on it.

    The service reaches this database on its own connection, not through Alembic, and every
    statement it writes from M4 onwards resolves against that session's search path. Remove
    the `ALTER ROLE` from 0001 and this is the test that fails — the pin in `env.py` covers
    migrations and nothing else.
    """
    with psycopg.connect(agent_url) as conn:
        conn.execute("CREATE TABLE service_probe (id integer PRIMARY KEY)")
        landed = conn.execute(
            "SELECT schemaname, tableowner FROM pg_tables WHERE tablename ="
            " 'service_probe'"
        ).fetchall()
        conn.rollback()

    assert landed == [("agent", "agent")]
