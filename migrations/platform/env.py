from alembic import context
from sqlalchemy import text

from vista.db import engine
from vista.models.platform import PlatformBase

target_metadata = PlatformBase.metadata


def run_migrations_online() -> None:
    with engine.connect() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS platform"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="platform",
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
