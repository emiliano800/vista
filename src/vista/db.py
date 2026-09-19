import re
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from vista.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

_SCHEMA_RE = re.compile(r"^t_[0-9a-f]{12}$")


def validate_tenant_schema(schema: str) -> str:
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"invalid tenant schema name: {schema!r}")
    return schema


@contextmanager
def platform_session():
    with SessionLocal() as session:
        session.execute(text("SET search_path TO platform"))
        yield session


@contextmanager
def tenant_session(schema: str):
    """Session scoped to a single tenant's schema. `platform` is intentionally
    excluded from search_path so tenant code cannot silently touch shared tables."""
    validate_tenant_schema(schema)
    with SessionLocal() as session:
        session.execute(text(f'SET search_path TO "{schema}"'))
        yield session


def set_tenant_search_path(session: Session, schema: str) -> None:
    validate_tenant_schema(schema)
    session.execute(text(f'SET search_path TO "{schema}"'))
