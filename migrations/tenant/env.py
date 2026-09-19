from alembic import context
from sqlalchemy import text

from vista.db import engine, validate_tenant_schema
from vista.models.tenant import TenantBase

target_metadata = TenantBase.metadata


def run_migrations_online() -> None:
    schema = context.get_x_argument(as_dictionary=True).get("schema")
    if not schema:
        raise SystemExit("tenant migrations require -x schema=<tenant_schema>")
    validate_tenant_schema(schema)

    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # Session-level SET survives the commit below; do NOT issue further
        # statements after commit or Alembic will assume the caller owns the
        # transaction and never commit the migration.
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=schema,
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
