import secrets
import uuid

from sqlalchemy import select

from tests.conftest import requires_db
from vista.db import platform_session, tenant_session
from vista.models.platform import Tenant, User
from vista.models.tenant import DealMembership

pytestmark = requires_db


def _add_user(tenant_id: str, email: str) -> tuple[dict, uuid.UUID]:
    token = secrets.token_hex(32)
    with platform_session() as session:
        user = User(tenant_id=uuid.UUID(tenant_id), email=email, api_token=token)
        session.add(user)
        session.commit()
        return {"Authorization": f"Bearer {token}"}, user.id


def _tenant_schema(tenant_id: str) -> str:
    with platform_session() as session:
        return session.scalar(select(Tenant.schema_name).where(Tenant.id == uuid.UUID(tenant_id)))


def test_non_member_cannot_access_deal(client, tenant_factory):
    owner_headers, tenant_id, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Heron"}, headers=owner_headers).json()

    outsider_headers, _ = _add_user(tenant_id, "analyst@firm.example.com")
    resp = client.get(f"/deals/{deal['id']}/documents", headers=outsider_headers)
    assert resp.status_code == 403


def test_viewer_cannot_upload_documents(client, tenant_factory):
    owner_headers, tenant_id, _ = tenant_factory()
    deal = client.post("/deals", json={"name": "Project Ibis"}, headers=owner_headers).json()

    viewer_headers, viewer_id = _add_user(tenant_id, "lp@firm.example.com")
    with tenant_session(_tenant_schema(tenant_id)) as session:
        session.add(DealMembership(deal_id=uuid.UUID(deal["id"]), user_id=viewer_id, role="viewer"))
        session.commit()

    # Viewer can list...
    assert client.get(f"/deals/{deal['id']}/documents", headers=viewer_headers).status_code == 200
    # ...but cannot upload.
    resp = client.post(f"/deals/{deal['id']}/documents", json={"filename": "notes.txt"}, headers=viewer_headers)
    assert resp.status_code == 403
