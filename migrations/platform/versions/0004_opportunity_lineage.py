"""platform: opportunity lineage (reviewer findings and runs an opportunity was built on)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("lineage", JSONB, nullable=False, server_default="{}"), schema="platform")


def downgrade() -> None:
    op.drop_column("opportunities", "lineage", schema="platform")
