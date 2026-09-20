"""Documents on screen during a recording: open/close markers, the snapshot's
object key and the state of the worker's text extraction."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("recordings", sa.Column("files", JSONB, nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("recordings", "files")
