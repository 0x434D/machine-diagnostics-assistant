"""One connection pool for the process.

§5.2: the analysis service reads views and cannot write, and the database enforces that
rather than convention. Nothing here issues a write.
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
