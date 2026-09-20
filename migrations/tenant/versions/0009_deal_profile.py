"""Portfolio companies carry a profile (industry, location, acquisition date)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("deals", sa.Column("profile", JSONB, nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("deals", "profile")
