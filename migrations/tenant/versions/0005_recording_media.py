"""Recording media (screenshots, screen video, raw events) uploaded to object storage,
and the employee's section edits kept as metadata on the recording."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recordings", sa.Column("media", JSONB, nullable=False, server_default="{}"))
    op.add_column("recordings", sa.Column("sections", JSONB, nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("recordings", "sections")
    op.drop_column("recordings", "media")
