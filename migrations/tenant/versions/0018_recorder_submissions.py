import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recorder_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("canonical_company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("upload_status", sa.String(16), nullable=False, server_default="uploading"),
        sa.Column("verified_artifacts", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("uploaded_by", "device_id", "source_id", name="uq_recorder_submission_source"),
        sa.CheckConstraint("upload_status IN ('uploading', 'accepted')", name="ck_recorder_submission_status"),
    )
    op.create_index("ix_recorder_submissions_uploaded_by", "recorder_submissions", ["uploaded_by"])


def downgrade() -> None:
    op.drop_table("recorder_submissions")
