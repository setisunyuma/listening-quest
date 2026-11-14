"""repair items (L01/L03)

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repair_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_id", sa.String(length=64), sa.ForeignKey("units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("term", sa.String(length=80), nullable=True),
        sa.Column("cause", sa.String(length=24), nullable=False),
        sa.Column("skill", sa.String(length=32), nullable=True),
        sa.Column("source_attempt_id", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=12), nullable=False),
        sa.Column("due_day", sa.Date(), nullable=True),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "unit_id", "utterance_id", "kind", "term", name="uq_repair_item"),
    )
    op.create_index("ix_repair_items_user_id", "repair_items", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_repair_items_user_id", table_name="repair_items")
    op.drop_table("repair_items")
