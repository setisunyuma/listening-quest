"""practice states keep three separate records: tried first, criteria met, opened by the main goal

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("practice_states", sa.Column("tried_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("practice_states", sa.Column("criteria_met_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("practice_states", sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("practice_states", sa.Column("goal_opened_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for c in ("goal_opened_at", "criteria", "criteria_met_at", "tried_at"):
        op.drop_column("practice_states", c)
