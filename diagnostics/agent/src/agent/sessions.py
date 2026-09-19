"""§5.2's four tables, and every statement this service writes against them.

Raw SQL and Pydantic row models, which is what the handbook asks for over an ORM: nothing
here mirrors the schema a second time, and migration 0001 remains the only description of
it.

**What addresses an exchange is the question's `seq`.** `POST /ask` names it on the wire
before the pipeline has run a single step, so the UI can reach the trace for an answer that
never arrives — and an id promised that early cannot be the answer's own row without
reserving one, which would mean writing an assistant message that says nothing and filling
it in later. The question's row exists the moment the question does. The trace and the
feedback hang off it, the answer is stored beside it, and a second question asked into the
same session while the first is still running takes the next free `seq` rather than the one
already promised to somebody.

Nothing here refuses anybody. `agent.app` decides that, because it is the layer that holds
the principal; `subject_of` tells it whose a session is before a question is written into
one, and `addressed` tells it the same plus whether the message being asked about exists.
Two questions, two statements — a caller reading a trace has a message to find and a caller
asking a question does not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from agent.config import Settings
from agent.records import Feedback, Trace


@dataclass(frozen=True)
class Addressed:
    """The two facts an endpoint addressed at (session, seq) needs before it answers.

    Both halves rather than a bare "may I have this": §14 keeps *I do not know this* and
    *I know it and it is not yours* apart, and one boolean would collapse them into a
    refusal that reads as a login bug for a day.
    """

    subject: str | None
    """`agent.sessions.subject` — the `sub` off the token whoever opened the session
    presented. Null only for a row written before M5 filled the column."""

    holds_message: bool


def _dsn(settings: Settings) -> str:
    """The connection string as the deployment provisions it.

    The role's password arrives through the environment the deployment sets, never through
    a literal (config.py says why). Empty means unprovisioned and is left off entirely: an
    empty string sent as a password authenticates as nothing rather than as no password.
    """
    if settings.role_password:
        return make_conninfo(settings.database_url, password=settings.role_password)
    return settings.database_url


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
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
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


async def subject_of(session_id: uuid.UUID, settings: Settings) -> str | None:
    """Whoever opened this session, or `None` when nothing has opened it.

    `remember` will not rewrite the column, so this cannot go stale between being read and
    being acted on: a session belongs to whoever opened it for as long as it exists.
    """
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
        cursor = await connection.execute(
            "SELECT subject FROM agent.sessions WHERE id = %s", (session_id,)
        )
        row = await cursor.fetchone()

    if row is None or row[0] is None:
        return None
    return str(row[0])


async def asked(
    session_id: uuid.UUID, question: str, now: datetime, settings: Settings
) -> int:
    """Store the question and return the `seq` this exchange is addressed by.

    The sequence number is computed inside the INSERT rather than read and then written, so
    two questions asked into one session cannot be handed the same one.
    """
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
        cursor = await connection.execute(
            "INSERT INTO agent.messages (session_id, seq, role, content, created_at)"
            " SELECT %(session)s, COALESCE(MAX(seq), 0) + 1, 'user', %(content)s,"
            " %(now)s FROM agent.messages WHERE session_id = %(session)s"
            " RETURNING seq",
            {"session": session_id, "content": question, "now": now},
        )
        row = await cursor.fetchone()
        assert row is not None, "RETURNING seq yielded no row"
        return int(row[0])


async def answered(
    session_id: uuid.UUID,
    seq: int,
    answer: str,
    trace: Trace,
    now: datetime,
    settings: Settings,
) -> None:
    """Store the answer and the trace of how it was reached.

    One transaction, because §5.2 buys an audit trail and an answer with no record of the
    tool calls behind it is the half of it nobody can check. `seq` addresses the question;
    the answer takes the next free number after it.
    """
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
        await connection.execute(
            "INSERT INTO agent.messages (session_id, seq, role, content, created_at)"
            " SELECT %(session)s, COALESCE(MAX(seq), 0) + 1, 'assistant', %(content)s,"
            " %(now)s FROM agent.messages WHERE session_id = %(session)s",
            {"session": session_id, "content": answer, "now": now},
        )
        await connection.execute(
            "INSERT INTO agent.traces"
            " (session_id, message_seq, sops_loaded, tool_calls, budget, timings)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                session_id,
                seq,
                trace.sops_loaded,
                Jsonb([call.model_dump() for call in trace.tool_calls]),
                Jsonb(trace.budget.model_dump()),
                Jsonb(trace.timings.model_dump()),
            ),
        )


async def addressed(
    session_id: uuid.UUID, seq: int, settings: Settings
) -> Addressed | None:
    """Whose session this is and whether it holds that message, or `None` for no session."""
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
        cursor = await connection.execute(
            "SELECT s.subject, EXISTS (SELECT 1 FROM agent.messages m"
            " WHERE m.session_id = s.id AND m.seq = %(seq)s)"
            " FROM agent.sessions s WHERE s.id = %(session)s",
            {"session": session_id, "seq": seq},
        )
        row = await cursor.fetchone()

    if row is None:
        return None
    subject, holds_message = row
    return Addressed(
        subject=None if subject is None else str(subject),
        holds_message=bool(holds_message),
    )


async def trace_of(session_id: uuid.UUID, seq: int, settings: Settings) -> Trace | None:
    """§7.2's trace for one message, or `None` when nothing recorded one.

    Validated on the way out rather than handed over as the four raw columns: a run that
    stopped recording a field would otherwise reach the UI as a key that is quietly absent.
    """
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
        cursor = await connection.execute(
            "SELECT sops_loaded, tool_calls, budget, timings FROM agent.traces"
            " WHERE session_id = %s AND message_seq = %s",
            (session_id, seq),
        )
        row = await cursor.fetchone()

    if row is None:
        return None
    sops, calls, budget, timings = row
    return Trace.model_validate(
        {
            "sops_loaded": sops,
            "tool_calls": calls,
            "budget": budget,
            "timings": timings,
        }
    )


async def feedback_on(
    session_id: uuid.UUID,
    seq: int,
    given: Feedback,
    now: datetime,
    settings: Settings,
) -> Feedback:
    """Record §7.2's answers for one message and return everything now stored for it.

    `COALESCE` per column is what makes the two questions independent: a submission that
    answers one of them leaves the other as it found it, which is the ordinary case — the
    operator reads the answer, says it was useful, and comes back an hour later having been
    to the machine. `created_at` is deliberately not touched on a second submission; it is
    when this message was first given feedback, and the column cannot say both.
    """
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as connection:
        cursor = await connection.execute(
            "INSERT INTO agent.feedback"
            " (session_id, message_seq, useful, matched_reality, comment, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (session_id, message_seq) DO UPDATE SET"
            "   useful = COALESCE(EXCLUDED.useful, feedback.useful),"
            "   matched_reality ="
            "     COALESCE(EXCLUDED.matched_reality, feedback.matched_reality),"
            "   comment = COALESCE(EXCLUDED.comment, feedback.comment)"
            " RETURNING useful, matched_reality, comment",
            (
                session_id,
                seq,
                given.useful,
                given.matched_reality,
                given.comment,
                now,
            ),
        )
        row = await cursor.fetchone()

    assert row is not None, "RETURNING yielded no row"
    useful, matched_reality, comment = row
    return Feedback(
        useful=useful,
        matched_reality=matched_reality,
        comment=None if comment is None else str(comment),
    )
