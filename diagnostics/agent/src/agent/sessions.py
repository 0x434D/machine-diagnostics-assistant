"""§5.2's `sessions` table, and the one column M5 fills: `subject`.

Migration 0001 typed `subject` as the OIDC `sub` and left it nullable, because there was no
issuer and a column defaulted to `"anonymous"` cannot be told apart from one nobody ever
wrote to. There is an issuer now (§10.5), so this is what writes it — and the row is what
turns §5.2's trace tables into an audit trail: whatever `messages` and `traces` come to hold
in M6, they hang off a session that already says whose it is.

Nothing else here. There is no transcript, no history query and no ownership check: §10.5 is
explicit that there are no per-resource rules, and a session id is unguessable by
construction (§6.10, and migration 0001's `gen_random_uuid()`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import psycopg
from psycopg.conninfo import make_conninfo

from agent.config import Settings


async def remember(
    subject: str,
    now: datetime,
    settings: Settings,
    session_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record the session this question belongs to, and return its id.

    A new session when the caller named none, and nothing when it named one that exists:
    `subject` belongs to whoever opened the session and a later question does not rewrite
    it. Raises if the database cannot be reached — a question answered with no record of who
    asked it is the shape of audit trail that is worse than none.
    """
    # The role's password arrives through the environment the deployment sets, never through
    # a literal (config.py says why). Empty means unprovisioned and is left off entirely: an
    # empty string sent as a password authenticates as nothing rather than as no password.
    dsn = settings.database_url
    if settings.role_password:
        dsn = make_conninfo(dsn, password=settings.role_password)

    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        if session_id is None:
            cursor = await connection.execute(
                "INSERT INTO agent.sessions (subject, created_at) VALUES (%s, %s)"
                " RETURNING id",
                (subject, now),
            )
            row = await cursor.fetchone()
            assert row is not None, "RETURNING id yielded no row"
            return uuid.UUID(str(row[0]))

        await connection.execute(
            "INSERT INTO agent.sessions (id, subject, created_at) VALUES (%s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING",
            (session_id, subject, now),
        )
        return session_id
