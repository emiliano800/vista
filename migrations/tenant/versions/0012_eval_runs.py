"""Persist answer-key evaluations so the dashboard can show the latest measured
precision/recall per phase and sector next to the spend/throughput numbers."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "eval_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("agent_key", sa.String(32), nullable=False),
        sa.Column("sector", sa.String(64), nullable=True),
        sa.Column("company", sa.String(64), nullable=True),
        sa.Column("division", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("predictions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trap_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("precision", sa.Float(), nullable=False, server_default="0"),
        sa.Column("recall", sa.Float(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_eval_runs_phase_created", "eval_runs", ["phase", "created_at"])
    op.create_index("ix_eval_runs_agent_key", "eval_runs", ["agent_key"])


def downgrade():
    op.drop_index("ix_eval_runs_agent_key", table_name="eval_runs")
    op.drop_index("ix_eval_runs_phase_created", table_name="eval_runs")
    op.drop_table("eval_runs")
