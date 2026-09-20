"""Give runs, findings and usage the dimensions the CFO views group by:
company, division, sector, agent_key. Lifts findings.company out of the
evidence JSONB (the key stays there so nothing that reads it breaks)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

AGENT_KEY_BY_RUN_TYPE = {
    "deal_analysis": "file_reviewer",
    "synthetic_discovery": "file_reviewer",
    "employee_discovery": "file_reviewer",
    "company_summary": "report_generator",
    "synthetic_analyze": "sector_merger",
    "recording_review": "recording_reviewer",
}


def upgrade():
    op.add_column("agent_runs", sa.Column("company", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("division", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("sector", sa.String(64), nullable=True))
    op.add_column("agent_runs", sa.Column("recording_id", UUID(as_uuid=True), sa.ForeignKey("recordings.id"), nullable=True))
    op.add_column("agent_runs", sa.Column("agent_key", sa.String(32), nullable=True))
    op.add_column("agent_runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("error", sa.Text(), nullable=True))
    op.create_index("ix_agent_runs_company", "agent_runs", ["company"])
    op.create_index("ix_agent_runs_sector", "agent_runs", ["sector"])
    op.create_index("ix_agent_runs_agent_key", "agent_runs", ["agent_key"])

    op.add_column("findings", sa.Column("company", sa.String(64), nullable=True))
    op.add_column("findings", sa.Column("agent_key", sa.String(32), nullable=True))
    op.create_index("ix_findings_company", "findings", ["company"])
    op.create_index("ix_findings_agent_key", "findings", ["agent_key"])

    op.add_column("usage_events", sa.Column("agent_key", sa.String(32), nullable=True))
    op.add_column("usage_events", sa.Column("company", sa.String(64), nullable=True))
    op.create_index("ix_usage_events_agent_key", "usage_events", ["agent_key"])
    op.create_index("ix_usage_events_company", "usage_events", ["company"])

    for run_type, key in AGENT_KEY_BY_RUN_TYPE.items():
        op.execute(sa.text("UPDATE agent_runs SET agent_key = :key WHERE run_type = :rt").bindparams(key=key, rt=run_type))
    op.execute(
        "UPDATE agent_runs r SET company = e.data->>'company', division = e.data->>'division' "
        "FROM agent_run_events e WHERE e.run_id = r.id AND e.seq = 1 AND e.data ? 'company'"
    )
    op.execute(
        "UPDATE agent_runs r SET sector = e.data->>'sector' "
        "FROM agent_run_events e WHERE e.run_id = r.id AND e.seq = 1 AND e.data ? 'sector'"
    )
    op.execute("UPDATE findings SET company = evidence->>'company' WHERE evidence ? 'company'")
    op.execute(
        "UPDATE findings f SET agent_key = r.agent_key, company = COALESCE(f.company, r.company) FROM agent_runs r WHERE r.id = f.run_id"
    )
    op.execute("UPDATE usage_events u SET agent_key = r.agent_key, company = r.company FROM agent_runs r WHERE r.id = u.run_id")


def downgrade():
    for name in ("ix_usage_events_company", "ix_usage_events_agent_key"):
        op.drop_index(name, table_name="usage_events")
    op.drop_column("usage_events", "company")
    op.drop_column("usage_events", "agent_key")
    for name in ("ix_findings_agent_key", "ix_findings_company"):
        op.drop_index(name, table_name="findings")
    op.drop_column("findings", "agent_key")
    op.drop_column("findings", "company")
    for name in ("ix_agent_runs_agent_key", "ix_agent_runs_sector", "ix_agent_runs_company"):
        op.drop_index(name, table_name="agent_runs")
    for col in ("error", "started_at", "agent_key", "recording_id", "sector", "division", "company"):
        op.drop_column("agent_runs", col)
