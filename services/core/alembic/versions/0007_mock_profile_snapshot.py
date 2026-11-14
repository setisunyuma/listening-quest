"""mock sets keep the profile they were built with (R05)

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # existing sets stay readable through their stored profile id (no silent switch to the current version)
    op.add_column("mock_sets", sa.Column("profile_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("mock_sets", "profile_snapshot")
