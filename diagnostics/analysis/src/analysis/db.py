"""One connection pool for the process.

§5.2: the analysis service reads views and cannot write. Since M3 that is enforced rather
than asserted — this pool connects as the `analysis` role, which holds USAGE on `read`,
SELECT on its views, and nothing anywhere else. A write, or a read of a table no view
covers, comes back as `psycopg.errors.InsufficientPrivilege` from the server.

**This docstring claimed all of that for two milestones while none of it was true**: every
table sat in `public` and this pool connected as the same superuser the gateway wrote with.
Migration `005_m3_read_layer.sql` is what made the sentence true and
`tests/test_read_layer.py` is what keeps it true; if either goes, so does this paragraph.

Queries name `read.` explicitly rather than leaning on a search path, so an unqualified
relation is a loud error instead of a lookup that happens to succeed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg_pool import ConnectionPool

from analysis.config import Settings

_pool: ConnectionPool | None = None


def pool(settings: Settings) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, open=True)
    return _pool


def reset_pool() -> None:
    """Drops the pool so a test can point the next one at a different database."""
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = None


@contextmanager
def connection(settings: Settings) -> Iterator[Connection]:
    with pool(settings).connection() as conn:
        yield conn
