"""A real Postgres with both migration sets applied, for the tests that need one.

Two suites do: `test_agent_schema.py`, which is about the ownership boundary, and
`test_trace_and_feedback.py`, which is about what §7.2's endpoints store and read back.
Neither can be answered against a fake — the first because a grant asserted against a
stand-in is a statement about the stand-in, the second because a round trip through a
column type is the thing being claimed.

The fixtures live here rather than in one of those files so the other does not import from
it, and are re-exported by `conftest.py` because that is where pytest looks. Module-scoped:
`test_agent_schema.py` applies and rolls back probe migrations against this database, and a
suite that shared one with it would be reading a schema another file is editing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.conninfo import conninfo_to_dict
from testcontainers.postgres import PostgresContainer

AGENT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = AGENT / "alembic.ini"

GATEWAY_MIGRATIONS = AGENT.parents[0] / "gateway" / "Gateway" / "Migrations"
"""The gateway's embedded SQL, read from the directory rather than listed.

The agent's schema shares an instance with it, and the database-level `search_path` that
makes `test_agent_schema.py` necessary is set by 005. Applying them is not fixture scenery:
without `ingest` and `read` in place there is nothing for the agent role to be refused.
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
