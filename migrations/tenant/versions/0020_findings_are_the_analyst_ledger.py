"""Findings are the one ledger every view reads.

The analyst workspace used to keep a mirror of each finding (`workspace_findings`) plus
a stub agent layer (`workspace_agents`, `workspace_agent_runs`) that the company
workspace and the recorder never saw. The mirror is retired: `findings` gains the three
columns the analyst pages needed from it (a firm-wide display ref, the firm company id,
the demo flag), and the analyst reads `findings` / `agent_runs` directly.

Data carried over: a mirrored finding's ref and its status (Open/Reviewed/Actioned/
Dismissed -> open/reviewed/actioned/dismissed) are copied onto the ledger finding with
the same title before the mirror tables are dropped.

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("ref", sa.String(16), nullable=True))
    op.add_column("findings", sa.Column("company_id", UUID(as_uuid=True), nullable=True))
    op.add_column("findings", sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_unique_constraint("uq_findings_ref", "findings", ["ref"])
    op.create_index("ix_findings_company_id", "findings", ["company_id"])
    conn = op.get_bind()
    mirrored = conn.execute(sa.text("SELECT ref, company_id, title, status FROM workspace_findings ORDER BY found_at, ref")).all()
    for ref, company_id, title, status in mirrored:
        target = conn.execute(
            sa.text("SELECT id FROM findings WHERE title = :title AND ref IS NULL ORDER BY created_at DESC LIMIT 1"), {"title": title}
        ).scalar()
        if target is None:
            continue
        conn.execute(
            sa.text(
                "UPDATE findings SET ref = :ref, company_id = :company, "
                "status = CASE WHEN status = 'open' THEN :status ELSE status END WHERE id = :id"
            ),
            {"ref": ref, "company": company_id, "status": (status or "open").lower(), "id": target},
        )
    for table in ("workspace_findings", "workspace_agent_runs", "workspace_agents"):
        op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "workspace_agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("ref", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="Active"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "workspace_agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("workspace_agents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ref", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="Complete"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("needs_review", sa.Integer, nullable=False, server_default="0"),
        sa.Column("model_cost", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "workspace_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("ref", sa.String(16), nullable=False, unique=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("workspace_agents.id"), nullable=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("workspace_agent_runs.id"), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="Medium"),
        sa.Column("status", sa.String(16), nullable=False, server_default="Open"),
        sa.Column("found_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.drop_index("ix_findings_company_id", table_name="findings")
    op.drop_constraint("uq_findings_ref", "findings", type_="unique")
    for col in ("synthetic_demo", "company_id", "ref"):
        op.drop_column("findings", col)
