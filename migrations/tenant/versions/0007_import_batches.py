"""Durable, company-scoped data ingestion and review."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "import_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("s3_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("as_of", sa.String(10), nullable=False),
        sa.Column("tables", JSONB, nullable=False),
        sa.Column("analysis", JSONB, nullable=False),
        sa.Column("events", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("deal_id", "content_hash"),
    )
    op.create_index("ix_import_batches_deal_id", "import_batches", ["deal_id"])


def downgrade():
    op.drop_table("import_batches")
