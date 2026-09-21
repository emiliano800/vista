"""Canonical policies, purchase orders / lines and inventory balances.

Widens the fact layer beyond customers/invoices/vendors/subscriptions so the
insurance-broking and industrial-goods source files can land as canonical rows
with the same row-level provenance.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015"
down_revision = "0014"
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
    op.add_column("vendors", sa.Column("source_vendor_id", sa.String(64), nullable=False, server_default=""))
    op.add_column("vendors", sa.Column("category", sa.String(128), nullable=False, server_default=""))
    op.add_column("vendors", sa.Column("payment_terms", sa.String(64), nullable=False, server_default=""))
    op.add_column("field_mappings", sa.Column("reason", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_id", UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=True, index=True),
        sa.Column("customer_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("source_policy_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_customer_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("policy_number", sa.String(64), nullable=False, index=True),
        sa.Column("line_of_business", sa.String(32), nullable=False, server_default=""),
        sa.Column("line_description", sa.String(128), nullable=False, server_default=""),
        sa.Column("carrier_code", sa.String(32), nullable=False, server_default="", index=True),
        sa.Column("carrier_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("expiration_date", sa.Date, nullable=True, index=True),
        sa.Column("term_months", sa.Integer, nullable=False, server_default="12"),
        sa.Column("annual_premium", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("commission_pct", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("expected_commission", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("billing_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_force"),
        sa.Column("producer_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("account_manager_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("surplus_lines", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("new_or_renewal", sa.String(16), nullable=False, server_default=""),
        sa.Column("experience_mod", sa.Numeric(6, 3), nullable=True),
        sa.Column("umbrella_limit", sa.Numeric(14, 2), nullable=True),
        *_provenance_columns(),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("vendors.id"), nullable=True, index=True),
        sa.Column("po_number", sa.String(64), nullable=False, index=True),
        sa.Column("source_supplier_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("supplier_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("po_date", sa.Date, nullable=True, index=True),
        sa.Column("buyer_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("payment_terms", sa.String(64), nullable=False, server_default=""),
        sa.Column("ship_via", sa.String(128), nullable=False, server_default=""),
        sa.Column("freight_terms", sa.String(64), nullable=False, server_default=""),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("approved_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("sent_method", sa.String(64), nullable=False, server_default=""),
        *_provenance_columns(),
    )
    op.create_table(
        "purchase_order_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("purchase_order_id", UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=True, index=True),
        sa.Column("po_number", sa.String(64), nullable=False, index=True),
        sa.Column("line_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column("item_id", sa.String(64), nullable=False, server_default="", index=True),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("manufacturer_part_number", sa.String(64), nullable=False, server_default="", index=True),
        sa.Column("ordered_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("uom", sa.String(16), nullable=False, server_default=""),
        sa.Column("unit_cost", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("extended_cost", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("need_by_date", sa.Date, nullable=True),
        sa.Column("promised_date", sa.Date, nullable=True),
        sa.Column("received_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("gl_account", sa.String(32), nullable=False, server_default=""),
        *_provenance_columns(),
    )
    op.create_table(
        "inventory_balances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("item_id", sa.String(64), nullable=False, index=True),
        sa.Column("warehouse", sa.String(64), nullable=False, server_default=""),
        sa.Column("bin_location", sa.String(64), nullable=False, server_default=""),
        sa.Column("on_hand_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("allocated_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("available_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("on_order_qty", sa.Numeric(14, 3), nullable=False, server_default="0"),
        sa.Column("uom", sa.String(16), nullable=False, server_default=""),
        sa.Column("unit_cost", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("extended_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("last_count_date", sa.Date, nullable=True),
        sa.Column("last_receipt_date", sa.Date, nullable=True),
        sa.Column("last_issue_date", sa.Date, nullable=True),
        sa.Column("as_of_date", sa.Date, nullable=True),
        *_provenance_columns(),
    )


def downgrade():
    for table in ("inventory_balances", "purchase_order_lines", "purchase_orders", "policies"):
        op.drop_table(table)
    for column in ("payment_terms", "category", "source_vendor_id"):
        op.drop_column("vendors", column)
    op.drop_column("field_mappings", "reason")
