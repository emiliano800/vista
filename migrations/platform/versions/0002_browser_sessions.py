"""Hash existing API keys and add expiring browser sessions."""

import hashlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for user_id, token in conn.execute(sa.text("SELECT id, api_token FROM platform.users")):
        conn.execute(
            sa.text("UPDATE platform.users SET api_token=:digest WHERE id=:id"),
            {"digest": hashlib.sha256(token.encode()).hexdigest(), "id": user_id},
        )
    op.alter_column("users", "api_token", new_column_name="api_token_hash", schema="platform")
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("platform.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="platform",
    )
    op.create_index("ix_browser_sessions_expires_at", "browser_sessions", ["expires_at"], schema="platform")


def downgrade():
    raise RuntimeError("Access-key hashing is irreversible; restore a backup or rotate keys instead")
