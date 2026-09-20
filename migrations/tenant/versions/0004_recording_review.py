"""AI explanations per recording section and the employee's approve/fix/explain decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recording_review_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("recording_id", UUID(as_uuid=True), sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("section", JSONB, nullable=False, server_default="{}"),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("label", sa.Text, nullable=False, server_default=""),
        sa.Column("explanation", sa.Text, nullable=False, server_default=""),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("unclear", JSONB, nullable=False, server_default="[]"),
        sa.Column("questions", JSONB, nullable=False, server_default="[]"),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("explained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("final_label", sa.Text, nullable=False, server_default=""),
        sa.Column("final_note", sa.Text, nullable=False, server_default=""),
        sa.Column("answers", JSONB, nullable=False, server_default="[]"),
        sa.Column("resolved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("recording_id", "item_id"),
    )
    op.create_index("ix_recording_review_items_recording_id", "recording_review_items", ["recording_id"])


def downgrade():
    op.drop_table("recording_review_items")
