"""employee agents, findings, company summaries; agent_runs run_type

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("role_title", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "employee_agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("scopes", JSONB, nullable=False, server_default="[]"),
        sa.Column("schedule", sa.String(16), nullable=False, server_default="daily"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "agent_runs",
        sa.Column("run_type", sa.String(32), nullable=False, server_default="deal_analysis"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("employee_agent_id", UUID(as_uuid=True), sa.ForeignKey("employee_agents.id"), nullable=True),
    )
    op.alter_column("agent_runs", "deal_id", nullable=True)
    op.create_table(
        "findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("employee_agents.id"), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("evidence", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_findings_status", "findings", ["status"])
    op.create_table(
        "company_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("stats", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("company_summaries")
    op.drop_table("findings")
    op.drop_column("agent_runs", "employee_agent_id")
    op.drop_column("agent_runs", "run_type")
    op.alter_column("agent_runs", "deal_id", nullable=False)
    op.drop_table("employee_agents")
    op.drop_table("employees")
