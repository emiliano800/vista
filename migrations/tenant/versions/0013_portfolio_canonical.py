"""Canonical business state (customers, invoices, vendors, purchases, subscriptions, tasks)
plus the raw-source / import / provenance layer for the PE portfolio workspace.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _provenance_columns():
    return [
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("data_source_type", sa.String(32), nullable=False, server_default="manual_entry"),
        sa.Column("source_file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("import_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "customers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("source_customer_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("address_line_1", sa.String(255), nullable=False, server_default=""),
        sa.Column("address_line_2", sa.String(255), nullable=False, server_default=""),
        sa.Column("city", sa.String(128), nullable=False, server_default=""),
        sa.Column("state", sa.String(32), nullable=False, server_default=""),
        sa.Column("postal_code", sa.String(32), nullable=False, server_default=""),
        sa.Column("service_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_provenance_columns(),
    )
    op.create_table(
        "invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=True, index=True),
        sa.Column("customer_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("source_invoice_number", sa.String(64), nullable=False),
        sa.Column("issue_date", sa.Date, nullable=True),
        sa.Column("due_date", sa.Date, nullable=True, index=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("outstanding_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        *_provenance_columns(),
    )
    op.create_table(
        "vendors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("normalized_name", sa.String(255), nullable=False, index=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("contact_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_provenance_columns(),
    )
    op.create_table(
        "vendor_purchases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("vendors.id"), nullable=True, index=True),
        sa.Column("purchase_date", sa.Date, nullable=True, index=True),
        sa.Column("sku", sa.String(64), nullable=False, server_default="", index=True),
        sa.Column("item_description", sa.Text, nullable=False, server_default=""),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(32), nullable=False, server_default=""),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        *_provenance_columns(),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(128), nullable=False, server_default=""),
        sa.Column("monthly_cost", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("annual_cost", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("seat_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("renewal_date", sa.Date, nullable=True),
        sa.Column("contract_end_date", sa.Date, nullable=True),
        sa.Column("restrictions_notes", sa.Text, nullable=False, server_default=""),
        *_provenance_columns(),
    )
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("ref", sa.String(16), nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("category", sa.String(64), nullable=False, server_default="Integration"),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("assignee", sa.String(255), nullable=False, server_default=""),
        sa.Column("priority", sa.String(16), nullable=False, server_default="Medium"),
        sa.Column("status", sa.String(16), nullable=False, server_default="Open"),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("outcome", sa.String(64), nullable=True),
        sa.Column("outcome_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("realized_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "source_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("file_size", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="stored"),
    )
    op.create_table(
        "import_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("source_file_id", UUID(as_uuid=True), sa.ForeignKey("source_files.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploaded"),
        sa.Column("dataset_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("detection_confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("sheet_name", sa.String(128), nullable=False, server_default="Sheet1"),
        sa.Column("columns", JSONB, nullable=False, server_default="[]"),
        sa.Column("processor", sa.String(32), nullable=False, server_default="demo"),
        sa.Column("records_detected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_imported", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_needing_review", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "field_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("import_job_id", UUID(as_uuid=True), sa.ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_column", sa.String(255), nullable=False),
        sa.Column("example_value", sa.Text, nullable=False, server_default=""),
        sa.Column("target_entity", sa.String(32), nullable=False),
        sa.Column("target_field", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("approved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("import_job_id", "source_column"),
    )
    op.create_table(
        "import_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("import_job_id", UUID(as_uuid=True), sa.ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_row", sa.Integer, nullable=False),
        sa.Column("raw_record", JSONB, nullable=False, server_default="{}"),
        sa.Column("normalized_record", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="staged"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1"),
        sa.Column("exception_reason", sa.Text, nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("import_job_id", "source_row"),
    )
    op.create_table(
        "import_exceptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("import_job_id", UUID(as_uuid=True), sa.ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("import_record_id", UUID(as_uuid=True), sa.ForeignKey("import_records.id"), nullable=True),
        sa.Column("ref", sa.String(16), nullable=False, server_default=""),
        sa.Column("exception_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("candidate_matches", JSONB, nullable=False, server_default="[]"),
        sa.Column("detail", JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(32), nullable=True),
        sa.Column("resolved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "record_provenance",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("field_name", sa.String(64), nullable=True),
        sa.Column("source_file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_filename", sa.String(512), nullable=False, server_default=""),
        sa.Column("sheet_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("row_number", sa.Integer, nullable=True),
        sa.Column("source_column", sa.String(255), nullable=True),
        sa.Column("raw_value", JSONB, nullable=False, server_default="{}"),
        sa.Column("normalized_value", JSONB, nullable=False, server_default="{}"),
        sa.Column("import_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1"),
        sa.Column("human_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

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


def downgrade():
    for table in (
        "workspace_findings",
        "workspace_agent_runs",
        "workspace_agents",
        "record_provenance",
        "import_exceptions",
        "import_records",
        "field_mappings",
        "import_jobs",
        "source_files",
        "tasks",
        "subscriptions",
        "vendor_purchases",
        "vendors",
        "invoices",
        "customers",
    ):
        op.drop_table(table)
