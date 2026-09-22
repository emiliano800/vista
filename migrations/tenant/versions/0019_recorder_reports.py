import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recorder_submissions", sa.Column("analysis_status", sa.String(16), nullable=False, server_default="not_started"))
    op.add_column("recorder_submissions", sa.Column("analysis_run_id", UUID(as_uuid=True), nullable=True))
    op.add_column("recorder_submissions", sa.Column("analysis_error", sa.Text, nullable=True))
    op.create_check_constraint(
        "ck_recorder_submission_analysis",
        "recorder_submissions",
        "analysis_status IN ('not_started', 'queued', 'running', 'succeeded', 'failed')",
    )
    op.create_table(
        "recorder_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("recorder_submissions.id"), nullable=False, unique=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_company_id", UUID(as_uuid=True), nullable=True),
        sa.Column("workspace", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("coverage", JSONB, nullable=False, server_default="{}"),
        sa.Column("observed", JSONB, nullable=False, server_default="{}"),
        sa.Column("interpretation", JSONB, nullable=False, server_default="{}"),
        sa.Column("questions", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_recorder_report_status"),
    )
    op.create_index("ix_recorder_reports_uploaded_by", "recorder_reports", ["uploaded_by"])
    op.create_index("ix_recorder_reports_status", "recorder_reports", ["status"])


def downgrade() -> None:
    op.drop_table("recorder_reports")
    op.drop_constraint("ck_recorder_submission_analysis", "recorder_submissions", type_="check")
    op.drop_column("recorder_submissions", "analysis_error")
    op.drop_column("recorder_submissions", "analysis_run_id")
    op.drop_column("recorder_submissions", "analysis_status")
