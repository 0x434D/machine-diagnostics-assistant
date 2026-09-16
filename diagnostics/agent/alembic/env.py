"""How the agent's migrations reach the database, and as whom.

Everything here exists because `agent.*` shares an instance with the gateway's schemas.

**Every migration run lands in `agent`, whoever is connected.** `005_m3_read_layer.sql`
sets `search_path = ingest, public` on the *database*, which applies to every role that
connects, and its own warning says what that costs a second owner: an unqualified
`CREATE TABLE` from a migration lands in the gateway's schema, and nothing fails until the
gateway's fixtures truncate it. Revision 0001 answers it with `ALTER ROLE agent SET
search_path = agent` — a role-level setting overrides a database-level one — but a role
setting can only defend sessions of that role, and the owner has to apply 0001 because a role
cannot create itself. So the documented bootstrap is two commands differing in one
environment variable, and 005's failure is one typo away from being reachable again: run the
owner's line at `head` and revision 0002 creates its table in `ingest`.

`pin_search_path` closes that by construction. The landing schema stops depending on who
connects, on which revision is being applied, and on whether the person applying it read
`alembic.ini` — every revision this environment applies, including ones nobody has written
yet, lands in `agent`. The `ALTER ROLE` in 0001 stays and is not redundant: it covers every
`agent` session that is *not* a migration — the service's own connections, and anyone with a
psql prompt — which this module never sees.

What neither closes is ownership. A revision applied with the owner's credentials creates
tables owned by the owner, so each revision hands what it creates to `agent` the way 0001
does. That failure is loud where the other was silent: the service is refused at its first
write rather than reading a table the gateway is truncating.

**The version table lives in `agent`, named rather than left to the pin.** It is a table
like any other, and Alembic's bookkeeping about the agent's schema belongs in the agent's
schema — not wherever a search path happens to point on the day someone edits one of these
two statements.

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
    # `disable_existing_loggers` defaults to True, which would silence every logger already
    # configured in this process — including `agent.pipeline`, whose per-call tool log is
    # §6.1 step 5's "every call logged". A migration run in-process (the tests do, and so
    # would a service that migrated at startup) would otherwise turn that log off for the
    # rest of the run, silently and from an unrelated module.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


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


def pin_search_path(connection: Connection) -> None:
    """Put this connection's unqualified names in `agent`, whichever role holds it.

    The session setting outranks both the database default 005 set and the role default 0001
    sets, so this is the one statement that makes where a revision lands a property of the
    migration rather than of the credentials it was handed. It is deliberately not a
    convention for each revision to remember — a line in a template reaches the revisions
    generated from it, and 0001 was written by hand.
    """
    connection.execute(text("SET search_path TO agent"))


def ensure_schema(connection: Connection) -> None:
    """Create `agent` if it is absent, so Alembic has somewhere to put its version table.

    Checked rather than `CREATE SCHEMA IF NOT EXISTS`: in the steady state this runs on a
    connection as `agent`, which holds no CREATE on the database and would be refused for a
    schema that is already there.
    """
    if (
        connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": SCHEMA}
        ).first()
        is None
    ):
        connection.execute(text("CREATE SCHEMA agent"))


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
        pin_search_path(connection)
        ensure_schema(connection)
        # One commit for both, and it is about neither of them. SQLAlchemy opens a
        # transaction on the first statement and Alembic joins whatever transaction it finds
        # rather than starting its own — so anything left open here swallows every revision
        # that follows, and they are rolled back when this connection closes. Nothing fails:
        # the upgrade logs every revision it ran and the database has none of them.
        connection.commit()
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
