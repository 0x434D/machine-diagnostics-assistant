"""How the agent's migrations reach the database, and as whom.

Three things here exist because `agent.*` shares an instance with the gateway's schemas.

**Who connects is the mechanism.** `005_m3_read_layer.sql` sets `search_path = ingest,
public` on the *database*, which applies to every role that connects, and its own warning
says what that costs a second owner: an unqualified `CREATE TABLE` from a migration lands in
the gateway's schema. Revision 0001 answers it with `ALTER ROLE agent SET search_path =
agent` — a role-level setting overrides a database-level one — and that only bites for
sessions authenticated as `agent`. So the migration runner is `agent` rather than the owner
from 0002 onwards — 0001 is the one revision it cannot apply, being the one that creates it.
`alembic.ini` carries the two commands that follow from that.

**The version table lives in `agent`.** It is a table like any other and unqualified it
would land wherever the search path points, which for the owner's bootstrap connection is
`ingest`. Alembic's bookkeeping about the agent's schema belongs in the agent's schema.

**Which is why the schema is created here and not in 0001.** Alembic creates its version
table before the first revision runs, so the schema holding it cannot be created by a
revision. This module does the one thing that has to happen first and nothing else: who owns
that schema, who may use it and what is in it are all 0001's.
"""

from __future__ import annotations

from logging.config import fileConfig

from agent.config import Settings
from alembic import context
from sqlalchemy import Connection, create_engine, text

SCHEMA = "agent"

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> str:
    """The URL this run migrates, in the form SQLAlchemy needs to reach psycopg.

    AGENT_DATABASE_URL and nothing else, so that which role a migration runs as is a
    property of the environment that started it. Compose's bootstrap hook overrides that
    variable for its own process, which is the whole of how the two identities are chosen.

    A bare `postgresql://` resolves to psycopg2, which this stack does not install — one
    Postgres driver, and it is the one the analysis service already uses. Compose, the
    tests and `agent.config` all speak the plain scheme, so the mapping is here rather than
    in each of them.
    """
    return Settings().database_url.replace("postgresql://", "postgresql+psycopg://", 1)


def ensure_schema(connection: Connection) -> None:
    """Create `agent` if it is absent, so Alembic has somewhere to put its version table.

    Checked rather than `CREATE SCHEMA IF NOT EXISTS`: in the steady state this runs on a
    connection as `agent`, which holds no CREATE on the database and would be refused for a
    schema that is already there.

    The commit is not optional and not about this statement. SQLAlchemy opens a transaction
    on the first query, and Alembic joins whatever transaction it finds rather than starting
    one — so a check left open here makes the migrations that follow part of it, and they
    are rolled back when this connection closes. Nothing fails: the upgrade logs every
    revision it ran and the database has none of them.
    """
    if (
        connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": SCHEMA}
        ).first()
        is None
    ):
        connection.execute(text("CREATE SCHEMA agent"))
    connection.commit()


def provision_role_password(connection: Connection) -> None:
    """Give the `agent` role the password this deployment reaches it with.

    0001 creates it able to log in and with no password. Whoever owns the database sets one
    afterwards, and for this schema that is whoever runs the bootstrap revision — the same
    two acts in the same order the gateway performs for `analysis` at boot (Program.cs:
    `ApplySchemaAsync`, then `SetAnalysisRolePasswordAsync`).

    Skipped for the steady-state runner, which *is* `agent`: handing a role its own password
    proves nothing and would put the credential on the wire on every migration run.

    The consequence the gateway's own comment states, restated because it is the same one:
    `ALTER ROLE` takes no parameters, so the password reaches the statement as a literal and
    is visible in `pg_stat_activity.query` while this runs. Quoted by the server's own
    `format(%L)` in a round trip rather than by an escape written here — this text is
    executed with the owner's rights, and a hand-rolled quote that is wrong once is an
    injection rather than a bug.
    """
    password = Settings().role_password
    if not password:
        return
    if connection.execute(text("SELECT current_user")).scalar() == "agent":
        return
    statement = connection.execute(
        # CAST, because `format`'s arguments are `anyelement` and a bare parameter leaves
        # the server with no type to resolve.
        text("SELECT format('ALTER ROLE agent PASSWORD %L', CAST(:password AS text))"),
        {"password": password},
    ).scalar_one()
    connection.execute(text(str(statement)))
    connection.commit()


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        ensure_schema(connection)
        context.configure(
            connection=connection,
            version_table_schema=SCHEMA,
            # No metadata and no autogenerate: §8 keeps the DDL as plain SQL both stacks can
            # read, and this service models no table in an ORM.
            target_metadata=None,
        )
        with context.begin_transaction():
            context.run_migrations()
        provision_role_password(connection)
    engine.dispose()


if context.is_offline_mode():
    # `--sql` emits a script without ever connecting, and both of the decisions above are
    # about the connection: which role holds it, and what already exists on the other end.
    # A generated script would silently be one for a database this one is not.
    raise RuntimeError(
        "offline mode is not supported: the schema check and the role these migrations run "
        "as are both properties of a live connection"
    )

run_migrations_online()
