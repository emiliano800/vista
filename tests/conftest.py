import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import vista.db as db
from vista.main import app


def _db_available() -> bool:
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="Postgres not reachable (start docker compose)"
)


@pytest.fixture(scope="session")
def client():
    from vista.tenancy import migrate_platform

    migrate_platform()
    return TestClient(app)


@pytest.fixture()
def tenant_factory(client):
    """Provision a fresh tenant and return (headers, tenant_id, user_id)."""

    def _create(name: str | None = None):
        name = name or f"firm-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/tenants", json={"name": name, "owner_email": f"owner@{name}.example.com"}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return (
            {"Authorization": f"Bearer {body['api_token']}"},
            body["tenant_id"],
            body["owner_user_id"],
        )

    return _create
