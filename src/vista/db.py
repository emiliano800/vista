import re
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from vista.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

_SCHEMA_RE = re.compile(r"^t_[0-9a-f]{12}$")


def validate_tenant_schema(schema: str) -> str:
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"invalid tenant schema name: {schema!r}")
    return schema


def _pin_search_path(session: Session, schema: str) -> None:
    """`SET search_path` is connection state, and a pooled session may sit on a different
    connection after every commit — one another helper left on `platform` or on another
    tenant. Re-assert the schema whenever this session begins a transaction, so a handler
    that commits mid-way (checkpoints, leases) keeps writing where it started."""
    statement = text(f'SET search_path TO "{schema}"')

    @event.listens_for(session, "after_begin")
    def _set(session_, transaction, connection):
        connection.execute(statement)

    session.execute(statement)


@contextmanager
def platform_session():
    with SessionLocal() as session:
        _pin_search_path(session, "platform")
        yield session


@contextmanager
def tenant_session(schema: str):
    """Session scoped to a single tenant's schema. `platform` is intentionally
    excluded from search_path so tenant code cannot silently touch shared tables."""
    validate_tenant_schema(schema)
    with SessionLocal() as session:
        _pin_search_path(session, schema)
        yield session


def set_tenant_search_path(session: Session, schema: str) -> None:
    validate_tenant_schema(schema)
    session.execute(text(f'SET search_path TO "{schema}"'))
