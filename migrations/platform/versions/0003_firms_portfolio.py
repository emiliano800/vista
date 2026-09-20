"""platform: PE firms, memberships, firm->company authorisation, opportunities, activity

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("home_tenant_id", UUID(as_uuid=True), sa.ForeignKey("platform.tenants.id"), nullable=False),
        sa.Column("counters", JSONB, nullable=False, server_default="{}"),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="platform",
    )
    op.create_table(
        "firm_memberships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("firm_id", UUID(as_uuid=True), sa.ForeignKey("platform.firms.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("platform.users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="analyst"),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "user_id"),
        schema="platform",
    )
    op.create_table(
        "firm_companies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("firm_id", UUID(as_uuid=True), sa.ForeignKey("platform.firms.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("platform.tenants.id"), nullable=False, unique=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("industry", sa.String(255), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("acquisition_date", sa.Date, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("analysis_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "slug"),
        schema="platform",
    )
    op.create_table(
        "opportunities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("firm_id", UUID(as_uuid=True), sa.ForeignKey("platform.firms.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ref", sa.String(16), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("company_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("sku", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="New"),
        sa.Column("observed_fact", sa.Text, nullable=False, server_default=""),
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("calculation", JSONB, nullable=False, server_default="[]"),
        sa.Column("potential_benefit", sa.Text, nullable=False, server_default=""),
        sa.Column("assumptions", JSONB, nullable=False, server_default="[]"),
        sa.Column("recommended_action", sa.Text, nullable=False, server_default=""),
        sa.Column("scenario_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("realized_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("generated_by", sa.String(32), nullable=False, server_default="deterministic_rule"),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("found_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("firm_id", "ref"),
        schema="platform",
    )
    op.create_table(
        "portfolio_activity",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("firm_id", UUID(as_uuid=True), sa.ForeignKey("platform.firms.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        schema="platform",
    )


def downgrade() -> None:
    for table in ("portfolio_activity", "opportunities", "firm_companies", "firm_memberships", "firms"):
        op.drop_table(table, schema="platform")
