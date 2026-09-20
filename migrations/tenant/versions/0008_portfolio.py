"""PE analyst portfolio: tasks, cross-company opportunities and activity."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "portfolio_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False, server_default="Integration"),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("assignee", sa.String(255), nullable=False, server_default=""),
        sa.Column("priority", sa.String(16), nullable=False, server_default="Medium"),
        sa.Column("due_date", sa.String(10), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="Open"),
        sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("outcome_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("realized_result", sa.Numeric(14, 2), nullable=True),
    )
    op.create_index("ix_portfolio_tasks_deal_id", "portfolio_tasks", ["deal_id"])

    op.create_table(
        "portfolio_opportunities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default="Software"),
        sa.Column("deal_ids", JSONB, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("potential_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="New"),
        sa.Column("found_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("fact", sa.Text, nullable=False, server_default=""),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("calculation", JSONB, nullable=False),
        sa.Column("benefit", sa.Text, nullable=False, server_default=""),
        sa.Column("assumptions", JSONB, nullable=False),
        sa.Column("next_action", sa.Text, nullable=False, server_default=""),
        sa.Column("realized_value", sa.Numeric(14, 2), nullable=True),
    )

    op.create_table(
        "portfolio_activity",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("actor", sa.String(255), nullable=False, server_default=""),
        sa.Column("ref", JSONB, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_portfolio_activity_deal_id", "portfolio_activity", ["deal_id"])
    op.create_index("ix_portfolio_activity_at", "portfolio_activity", ["at"])


def downgrade():
    op.drop_table("portfolio_activity")
    op.drop_table("portfolio_opportunities")
    op.drop_table("portfolio_tasks")
