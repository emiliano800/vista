import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A recorder that polls for computer-use work; presence is `last_seen_at`.
    op.create_table(
        "harness_devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False, server_default=""),
        sa.Column("capabilities", JSONB, nullable=False, server_default="{}"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "device_id", name="uq_harness_device"),
    )
    op.create_index("ix_harness_devices_company_seen", "harness_devices", ["company_id", "last_seen_at"])

    # An allow-listed HTTP endpoint set for a company (the minimal company-bound connection).
    op.create_table(
        "harness_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="http"),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('http')", name="ck_harness_connection_kind"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_harness_connection_status"),
        sa.UniqueConstraint("company_id", "name", name="uq_harness_connection_name"),
    )

    # One execution of one approved workflow version. Mutable header; the step ledger is agent_run_events.
    op.create_table(
        "workflow_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("workflow_version_id", UUID(as_uuid=True), sa.ForeignKey("workflow_versions.id"), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("agent_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False, unique=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="sandbox"),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("inputs", JSONB, nullable=False, server_default="{}"),
        sa.Column("limits", JSONB, nullable=False, server_default="{}"),
        sa.Column("steps_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("checkpoint", JSONB, nullable=False, server_default="{}"),
        sa.Column("harness_session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("pending_step_id", UUID(as_uuid=True), nullable=True),
        sa.Column("pending", JSONB, nullable=True),
        sa.Column("outcome", JSONB, nullable=True),
        sa.Column("stop_requested", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('dry_run', 'sandbox')", name="ck_workflow_run_mode"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_harness', 'waiting_for_human', 'succeeded', 'failed', 'stopped')",
            name="ck_workflow_run_status",
        ),
    )
    op.create_index("ix_workflow_runs_workflow_created", "workflow_runs", ["workflow_id", "created_at"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])

    # The employee's consented session for one run (lease held by one device).
    op.create_table(
        "harness_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_run_id", UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id"), nullable=False, unique=True),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kinds", JSONB, nullable=False, server_default="[]"),
        sa.Column("capabilities", JSONB, nullable=False, server_default="{}"),
        sa.Column("consent", JSONB, nullable=False),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'closed')", name="ck_harness_session_status"),
    )
    op.create_foreign_key("fk_workflow_runs_session", "workflow_runs", "harness_sessions", ["harness_session_id"], ["id"])

    # The mailbox: step requests the recorder pulls and answers. Every row is also a tool_call event.
    op.create_table(
        "harness_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_run_id", UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("harness_session_id", UUID(as_uuid=True), sa.ForeignKey("harness_sessions.id"), nullable=True),
        sa.Column("harness", sa.String(16), nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'claimed', 'done', 'failed', 'expired')", name="ck_harness_step_status"),
        sa.UniqueConstraint("workflow_run_id", "seq", name="uq_harness_step_seq"),
    )
    op.create_index("ix_harness_steps_session_status", "harness_steps", ["harness_session_id", "status"])


def downgrade() -> None:
    op.drop_table("harness_steps")
    op.drop_constraint("fk_workflow_runs_session", "workflow_runs", type_="foreignkey")
    op.drop_table("harness_sessions")
    op.drop_table("workflow_runs")
    op.drop_table("harness_connections")
    op.drop_table("harness_devices")
