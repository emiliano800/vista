"""Suggested workflows the recorder inferred for a session (workflows.json),
submitted with the report so the workspace holds report + workflows in Postgres."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recordings", sa.Column("workflows", JSONB, nullable=True))


def downgrade():
    op.drop_column("recordings", "workflows")
