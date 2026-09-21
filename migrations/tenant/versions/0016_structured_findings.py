"""Structured findings: machine-readable type, affected canonical entities and the
effect a finding has on downstream analysis, so the Sector Merger can intersect
reviewer findings with candidate evidence deterministically instead of reading prose.

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("findings", sa.Column("finding_type", sa.String(64), nullable=True))
    op.add_column("findings", sa.Column("severity", sa.String(16), nullable=True))
    op.add_column("findings", sa.Column("effect", sa.String(16), nullable=True))
    op.add_column("findings", sa.Column("blocking", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("findings", sa.Column("affected_entities", JSONB, nullable=False, server_default="[]"))
    op.create_index("ix_findings_finding_type", "findings", ["finding_type"])


def downgrade():
    op.drop_index("ix_findings_finding_type", table_name="findings")
    for col in ("affected_entities", "blocking", "effect", "severity", "finding_type"):
        op.drop_column("findings", col)
