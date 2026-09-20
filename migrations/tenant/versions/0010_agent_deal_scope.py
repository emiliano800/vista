"""Scope employee agents to a portfolio company so the workspace can group them."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employee_agents", sa.Column("deal_id", UUID(as_uuid=True), sa.ForeignKey("deals.id"), nullable=True))
    op.create_index("ix_employee_agents_deal_id", "employee_agents", ["deal_id"])


def downgrade():
    op.drop_index("ix_employee_agents_deal_id", table_name="employee_agents")
    op.drop_column("employee_agents", "deal_id")
