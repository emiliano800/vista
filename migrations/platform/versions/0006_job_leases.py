"""platform: jobs.lease_owner / jobs.lease_until — the worker that claimed a job holds
a lease on it and renews it while the handler runs. A job whose lease has lapsed was
claimed by a worker that stopped answering (a deploy, a crash); the reaper re-queues
it (or fails it once attempts are spent) instead of leaving it `running` forever.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lease_owner", sa.String(length=128), nullable=True), schema="platform")
    op.add_column("jobs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True), schema="platform")
    op.create_index("ix_platform_jobs_status_run_at", "jobs", ["status", "run_at"], schema="platform")


def downgrade() -> None:
    op.drop_index("ix_platform_jobs_status_run_at", table_name="jobs", schema="platform")
    op.drop_column("jobs", "lease_until", schema="platform")
    op.drop_column("jobs", "lease_owner", schema="platform")
