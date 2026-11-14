"""unit reports and missed lines

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unit_reports",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_id", sa.String(length=64), sa.ForeignKey("units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_unit_reports_user_id", "unit_reports", ["user_id"])
    op.create_index("ix_unit_reports_unit_id", "unit_reports", ["unit_id"])
    op.create_table(
        "missed_lines",
        sa.Column("user_id", sa.String(length=64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_id", sa.String(length=64), sa.ForeignKey("units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("utterance_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "unit_id", "utterance_id"),
    )


def downgrade() -> None:
    op.drop_table("missed_lines")
    op.drop_index("ix_unit_reports_unit_id", table_name="unit_reports")
    op.drop_index("ix_unit_reports_user_id", table_name="unit_reports")
    op.drop_table("unit_reports")
