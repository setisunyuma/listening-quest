"""plan slots become tasks: kind, context, payload, result, carry-over

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # existing slots are problem units shown in any context, with no task data: the defaults say exactly that
    op.add_column("plan_slots", sa.Column("kind", sa.String(length=16), nullable=False, server_default="unit"))
    op.add_column("plan_slots", sa.Column("context", sa.String(length=8), nullable=False, server_default="any"))
    op.add_column("plan_slots", sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"))
    op.add_column("plan_slots", sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"))
    op.add_column("plan_slots", sa.Column("carry_reason", sa.String(length=200), nullable=True))
    op.add_column("plan_slots", sa.Column("carried_from", sa.String(length=64), nullable=True))
    for c in ("kind", "context", "payload", "result"):
        op.alter_column("plan_slots", c, server_default=None)


def downgrade() -> None:
    for c in ("carried_from", "carry_reason", "result", "payload", "context", "kind"):
        op.drop_column("plan_slots", c)
