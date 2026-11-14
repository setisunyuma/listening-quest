"""main goal, goal decisions, readiness snapshots, practice task states (統合指示書 A01 / G01 / P01)

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

J = postgresql.JSONB(astext_type=sa.Text())
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # kept apart from the per-mode learning history: changing the main goal never rewrites reviews or attempts
    op.create_table(
        "learning_goals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),  # current | trial | maintained
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("transition_from", sa.String(32), nullable=True),
        sa.Column("transition_started_day", sa.Date(), nullable=True),
        sa.Column("since", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "mode", name="uq_learning_goal"),
    )
    op.create_table(
        "goal_decisions",
        sa.Column("id", sa.String(64), primary_key=True),  # client event id: a re-sent decision is recorded once
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("decision", sa.String(16), nullable=False),  # adopt | defer | keep | manual_switch | return | trial
        sa.Column("from_mode", sa.String(32), nullable=True),
        sa.Column("to_mode", sa.String(32), nullable=True),
        sa.Column("readiness", J, nullable=False, server_default="{}"),
        sa.Column("version", sa.String(48), nullable=False),
        sa.Column("learning_day", sa.Date(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "readiness_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("computed_day", sa.Date(), nullable=False),
        sa.Column("version", sa.String(48), nullable=False),
        sa.Column("result", J, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "mode", "computed_day", "version", name="uq_readiness_snapshot"),
    )
    op.create_table(
        "practice_states",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("practice_kind", sa.String(16), nullable=False),
        sa.Column("curriculum_version", sa.String(48), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),  # preview | unlocked | consolidating
        sa.Column("unlocked_at", TS, nullable=True),
        sa.Column("unlock_reason", sa.String(32), nullable=True),  # recommended | manual
        sa.Column("evidence", J, nullable=False, server_default="[]"),
        sa.Column("rule_version", sa.String(48), nullable=False),
        sa.Column("manual_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recommendation", J, nullable=False, server_default="{}"),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "practice_kind", "curriculum_version", name="uq_practice_state"),
    )


def downgrade() -> None:
    op.drop_table("practice_states")
    op.drop_table("readiness_snapshots")
    op.drop_table("goal_decisions")
    op.drop_table("learning_goals")
