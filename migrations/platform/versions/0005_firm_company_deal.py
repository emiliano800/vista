"""platform: firm_companies.deal_id — the Deal inside the company tenant that the
company workspace and the recorder scope by, so the analyst's company and the
employee's workspace resolve to the same rows by id instead of by name.

Backfill: for every linked company, reuse the tenant's Deal of the same name; when
the tenant has none, create one owned by the tenant's first user, or by the firm's
first member when the company tenant has no users yet. Tenants whose schema has no
deals table (never migrated) are left null and resolved lazily by
`vista.portfolio.service.ensure_company_deal`.

Revision ID: 0005
Revises: 0004
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("firm_companies", sa.Column("deal_id", UUID(as_uuid=True), nullable=True), schema="platform")
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT fc.id, fc.name, fc.tenant_id, fc.firm_id, t.schema_name FROM platform.firm_companies fc "
            "JOIN platform.tenants t ON t.id = fc.tenant_id WHERE fc.deal_id IS NULL"
        )
    ).all()
    for company_id, name, tenant_id, firm_id, schema in rows:
        if not schema.replace("_", "").isalnum():
            continue
        if conn.execute(sa.text("SELECT to_regclass(:t)"), {"t": f'"{schema}".deals'}).scalar() is None:
            continue
        deal_id = conn.execute(
            sa.text(f'SELECT id FROM "{schema}".deals WHERE name = :name ORDER BY created_at LIMIT 1'), {"name": name}
        ).scalar()
        if deal_id is None:
            owner = (
                conn.execute(
                    sa.text("SELECT id FROM platform.users WHERE tenant_id = :tenant ORDER BY created_at LIMIT 1"), {"tenant": tenant_id}
                ).scalar()
                or conn.execute(
                    sa.text("SELECT user_id FROM platform.firm_memberships WHERE firm_id = :firm ORDER BY created_at LIMIT 1"),
                    {"firm": firm_id},
                ).scalar()
            )
            if owner is None:
                continue
            deal_id = uuid.uuid4()
            conn.execute(
                sa.text(f"INSERT INTO \"{schema}\".deals (id, name, created_by, profile) VALUES (:id, :name, :owner, '{{}}')"),
                {"id": deal_id, "name": name, "owner": owner},
            )
        conn.execute(sa.text("UPDATE platform.firm_companies SET deal_id = :deal WHERE id = :id"), {"deal": deal_id, "id": company_id})


def downgrade() -> None:
    op.drop_column("firm_companies", "deal_id", schema="platform")
