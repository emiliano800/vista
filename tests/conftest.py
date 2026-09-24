import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import vista.db as db
from vista.config import settings
from vista.main import app

# Never spend real API credits in tests — force the stub model.
settings.openai_api_key = None
settings.provisioning_key = "test-provisioning-key"
settings.cookie_secure = False


def _db_available() -> bool:
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable (start docker compose)")


@pytest.fixture(scope="session")
def client():
    from vista.tenancy import migrate_platform

    migrate_platform()
    return TestClient(app)


def _tenant_ids(conn) -> dict:
    return dict(conn.execute(text("SELECT id, schema_name FROM platform.tenants")).all())


def _drop_tenant(conn, tenant_id, schema: str) -> None:
    """Remove one tenant and everything on the platform that points at it. The
    FKs to tenants are NO ACTION, so platform rows go first; firms cascade to
    memberships, companies, opportunities and activity, users to sessions."""
    db.validate_tenant_schema(schema)
    params = {"t": tenant_id}
    conn.execute(text("DELETE FROM platform.jobs WHERE tenant_id = :t"), params)
    conn.execute(text("DELETE FROM platform.firm_companies WHERE tenant_id = :t"), params)
    conn.execute(text("DELETE FROM platform.firms WHERE home_tenant_id = :t"), params)
    conn.execute(text("DELETE FROM platform.users WHERE tenant_id = :t"), params)
    conn.execute(text("DELETE FROM platform.tenants WHERE id = :t"), params)
    conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture(autouse=True)
def _drop_tenants_created_by_test():
    """Every tenant a test provisions — through `tenant_factory`, `provision_tenant`
    or the app itself — is dropped when the test ends, so the local database does
    not accumulate schemas at old revisions and the shared job queue stays empty.
    (Leaked tenants broke the scheduler test and starved `_drain()` on 2026-09-24.)"""
    if not _db_available():
        yield
        return
    with db.engine.begin() as conn:
        before = _tenant_ids(conn)
    yield
    db.engine.dispose()  # release pooled connections whose search_path is a dropped schema
    with db.engine.begin() as conn:
        for tenant_id, schema in _tenant_ids(conn).items():
            if tenant_id not in before:
                _drop_tenant(conn, tenant_id, schema)


@pytest.fixture()
def tenant_factory(client):
    """Provision a fresh tenant and return (headers, tenant_id, user_id)."""

    def _create(name: str | None = None):
        name = name or f"firm-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/tenants",
            headers={"X-Vista-Provisioning-Key": settings.provisioning_key},
            json={"name": name, "owner_email": f"owner@{name}.example.com"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return (
            {"Authorization": f"Bearer {body['api_token']}"},
            body["tenant_id"],
            body["owner_user_id"],
        )

    return _create
