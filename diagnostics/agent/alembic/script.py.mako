"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Plain SQL through `op.execute`, per §8: the DDL stays an artifact both stacks can read, and
this service models no table in an ORM. Names may be unqualified: `alembic/env.py` pins this
connection's search path to `agent` before any revision runs, whichever role holds it. Nothing
here may name a relation outside `agent`.

What the pin does not cover is ownership: a revision applied with the owner's credentials
creates tables owned by the owner, and the `agent` role could not write to one. So a revision
that creates a table hands it over, the way 0001 does:

    op.execute("ALTER TABLE agent.<table> OWNER TO agent")
"""

from __future__ import annotations

from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
