import argparse
import secrets
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from vista.db import platform_session, validate_tenant_schema
from vista.models.platform import Tenant, User

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config(name: str) -> Config:
    return Config(str(ALEMBIC_INI), ini_section=name)


def migrate_platform() -> None:
    command.upgrade(_alembic_config("platform"), "head")


def migrate_tenant_schema(schema: str) -> None:
    validate_tenant_schema(schema)
    cfg = _alembic_config("tenant")
    cfg.cmd_opts = argparse.Namespace(x=[f"schema={schema}"])
    command.upgrade(cfg, "head")


def migrate_all_tenants() -> None:
    with platform_session() as session:
        schemas = session.scalars(select(Tenant.schema_name)).all()
    for schema in schemas:
        migrate_tenant_schema(schema)


def provision_tenant(name: str, owner_email: str) -> tuple[Tenant, User, str]:
    """Create the tenant row, its schema (migrated to head), and an owner user.
    Returns (tenant, owner, api_token). The token is only returned once."""
    schema = f"t_{uuid.uuid4().hex[:12]}"
    token = secrets.token_hex(32)
    with platform_session() as session:
        tenant = Tenant(name=name, schema_name=schema)
        session.add(tenant)
        session.flush()
        owner = User(tenant_id=tenant.id, email=owner_email, api_token=token, role="admin")
        session.add(owner)
        session.commit()
    migrate_tenant_schema(schema)
    return tenant, owner, token
