"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Plain SQL through `op.execute`, per §8: the DDL stays an artifact both stacks can read, and
this service models no table in an ORM. Names may be unqualified — every revision after 0001
is applied by the `agent` role, whose search_path 0001 set — but nothing here may name a
relation outside `agent`.
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
