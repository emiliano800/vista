"""Uploaded, locally analyzed recording snapshots, scoped to a company/deal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recordings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_seconds", sa.Integer, nullable=False),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("s3_key", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("deal_id", "uploaded_by", "source_id"),
    )
    op.create_index("ix_recordings_deal_id", "recordings", ["deal_id"])


def downgrade():
    op.drop_table("recordings")
