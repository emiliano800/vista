import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("latest_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latest_version > 0", name="ck_workflow_latest_version"),
    )
    op.create_index("ix_workflows_company_id", "workflows", ["company_id"])
    op.create_table(
        "workflow_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workflow_id", "number", name="uq_workflow_version_number"),
        sa.CheckConstraint("number > 0", name="ck_workflow_version_number"),
    )
    op.create_table(
        "workflow_approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("workflow_versions.id"), nullable=False, unique=True),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_workflow_decision"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_workflow_history_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Workflow versions and decisions are immutable';
        END;
        $$
        """
    )
    for table in ("workflow_versions", "workflow_approvals"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_workflow_history_change()"
        )


def downgrade() -> None:
    op.drop_table("workflow_approvals")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
    op.execute("DROP FUNCTION prevent_workflow_history_change()")
