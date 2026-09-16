"""The agent's schema, the role that owns it, and the search_path 005 asked for.

Revision ID: 0001
Revises:
Create Date: 2026-09-16

§5.2's four tables — sessions, messages, traces, feedback — plus the role that owns them.
There is no `users` table on purpose: identity lives in the provider (§10.5) and a session
carries the `sub` claim, which is what turns the trace tables into an audit trail for free.

This is the one revision the `agent` role cannot apply, because it is the one that creates
it. The database owner applies it; everything after it is applied by `agent` itself, and
`alembic/env.py` explains why that is a correctness property rather than a deployment habit.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("sessions", "messages", "traces", "feedback")


def upgrade() -> None:
    # Three seconds, for the reason 005 gives: an ACCESS EXCLUSIVE acquisition that queues
    # behind a long reader blocks every subsequent query on that table, so a migration that
    # waits is an outage while a migration that fails is a retry. Nothing below takes a lock
    # anyone is waiting on — every relation here is new — but §8 puts this at the top of
    # every migration rather than at the top of the ones that turn out to need it.
    op.execute("SET lock_timeout = '3s'")

    # LOGIN and no password, exactly as 005 creates `analysis`: a literal here is a
    # credential in the repository, and a deployment that never provisions one should be
    # unreachable rather than reachable by whoever guesses first. Whoever owns the database
    # sets it afterwards — Compose through the migration step, the tests for their own
    # container. Never re-created and never reset here, which would lock out a running agent
    # on the next deploy.
    op.execute("""
        DO $create_role$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent') THEN
            CREATE ROLE agent LOGIN;
          END IF;
        END
        $create_role$
    """)

    # **This line is what 005's warning asked for, and it is the whole of the mechanism.**
    #
    # 005 sets `search_path = ingest, public` on the *database*, because the gateway's own
    # migrations name their tables unqualified and re-run on every boot. A database-level
    # default applies to every role that connects, not only the gateway's — so an
    # unqualified `CREATE TABLE` in a later revision of *this* directory would create the
    # agent's table in the gateway's schema. Nothing about that fails: the migration
    # succeeds, the table exists, the agent reads and writes it, and the first symptom is
    # the gateway's test fixtures truncating it.
    #
    # A role-level setting overrides a database-level one, and it is applied when a session
    # starts for that role — not by SET ROLE, and not by anything a client can forget. That
    # is why the migration runner from 0002 onwards is `agent` itself: this setting protects
    # `agent` sessions and only those.
    op.execute("ALTER ROLE agent SET search_path = agent")

    # `env.py` created this schema if it was missing, because Alembic's version table has to
    # exist before the first revision can run. Its ownership is settled here.
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")
    op.execute("ALTER SCHEMA agent OWNER TO agent")

    # Alembic's own bookkeeping, created on the owner's bootstrap connection moments ago.
    # `agent` applies every revision after this one and has to be able to write it.
    op.execute("ALTER TABLE agent.alembic_version OWNER TO agent")

    # Qualified, because this revision alone runs as the owner, whose search path is the
    # database's. From 0002 the role setting above does this job.
    #
    # `id` is unguessable in the database rather than in whatever writes it (§6.10): until
    # M5 there is no login, so the session id is the only thing standing between a session
    # and whoever asks for it by number. `created_at` deliberately has no default — this
    # system runs on an injected clock (CLAUDE.md), and a second clock in the database is
    # one no test can move.
    #
    # `subject` is the OIDC `sub` claim §5.2 names. It was nullable because there was no
    # issuer to state one; M5 built the validating half of §10.5 and `agent/sessions.py`
    # now writes this column from the token the asker presented, so that sentence is no
    # longer true and this one replaces it.
    #
    # It stays nullable all the same. A column is not retyped by editing the migration that
    # created it -- that is a new revision, and nobody has asked for one while the only path
    # that writes the table always sets the column. Never defaulted to a placeholder: a
    # column that always holds "anonymous" cannot be told apart from one nobody ever wrote
    # to, and this column is what an audit trail is read through.
    op.execute("""
        CREATE TABLE agent.sessions (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          subject    text,
          created_at timestamptz NOT NULL
        )
    """)

    # The transcript, and only the transcript. A tool call is not a message: it is machinery
    # and it belongs to `traces` below, which is what makes the CHECK a statement about the
    # conversation rather than a guess at a vocabulary.
    op.execute("""
        CREATE TABLE agent.messages (
          session_id uuid        NOT NULL REFERENCES agent.sessions (id) ON DELETE CASCADE,
          seq        integer     NOT NULL,
          role       text        NOT NULL,
          content    text        NOT NULL,
          created_at timestamptz NOT NULL,
          PRIMARY KEY (session_id, seq),
          CONSTRAINT messages_role_is_a_side_of_the_conversation
            CHECK (role IN ('user', 'assistant'))
        )
    """)

    # One row per answered message: the SOPs routing loaded, every tool call with its
    # arguments, what the budget allowed and what it spent, and where the time went.
    #
    # `sops_loaded` is ids, not text — §6.5 checks a cited SOP against the set routing
    # actually loaded, and that check is over ids. The other three are jsonb because each is
    # a record of one run's machinery rather than a relation anyone queries across runs; a
    # column per tool-call field would be a second model of the tool layer, drifting against
    # the first.
    op.execute("""
        CREATE TABLE agent.traces (
          session_id  uuid    NOT NULL,
          message_seq integer NOT NULL,
          sops_loaded text[]  NOT NULL,
          tool_calls  jsonb   NOT NULL,
          budget      jsonb   NOT NULL,
          timings     jsonb   NOT NULL,
          PRIMARY KEY (session_id, message_seq),
          FOREIGN KEY (session_id, message_seq)
            REFERENCES agent.messages (session_id, seq) ON DELETE CASCADE
        )
    """)

    # §7.2 asks two separate questions — was this useful, and did this match what you
    # actually found — and an operator may answer either, so both are nullable and a row
    # exists as soon as one of them is given. The second is the operator's own ground truth
    # and the more valuable signal; M7 is what reads it.
    op.execute("""
        CREATE TABLE agent.feedback (
          session_id      uuid        NOT NULL,
          message_seq     integer     NOT NULL,
          useful          boolean,
          matched_reality boolean,
          comment         text,
          created_at      timestamptz NOT NULL,
          PRIMARY KEY (session_id, message_seq),
          FOREIGN KEY (session_id, message_seq)
            REFERENCES agent.messages (session_id, seq) ON DELETE CASCADE
        )
    """)

    # Created by the owner, so owned by the owner until this runs. 005's warning names the
    # symptom it is guarding against in as many words: "agent tables owned by the wrong
    # role". Ownership is what `DROP OWNED BY` and a fixture that cleans up after itself
    # both act on.
    for table in TABLES:
        op.execute(f"ALTER TABLE agent.{table} OWNER TO agent")

    # No GRANT anywhere above, and that absence is the whole permission: a new schema grants
    # nothing to anyone, so `analysis` cannot see this and `agent` holds nothing in `ingest`
    # or `read`. The role reaches the plant's data the way every other reader does, through
    # the analysis service.


def downgrade() -> None:
    # The four tables and nothing else. Dropping the schema would take Alembic's own version
    # table with it, leaving a database that cannot say what it is; dropping the role would
    # lock out an agent that is still running, and the role is the one object here that
    # another deployment may have handed a password to.
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE agent.{table}")
