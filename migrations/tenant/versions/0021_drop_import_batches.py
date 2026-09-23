"""Retire the company workspace's JSON import batches.

`import_batches` held each company-side upload as JSON tables with a rule engine's
findings inside; nothing canonical was written and the analyst never saw it. The
company workspace now imports through the canonical contract (`source_files`,
`import_jobs`, canonical rows with `record_provenance`), the same path the analyst
uses, so the table goes. Downgrade recreates it empty.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("import_batches")


def downgrade() -> None:
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
