"""The most important tests in the repo: one firm must never see another firm's data."""
from tests.conftest import requires_db

pytestmark = requires_db


def test_tenants_cannot_see_each_others_deals(client, tenant_factory):
    headers_a, _, _ = tenant_factory()
    headers_b, _, _ = tenant_factory()

    deal = client.post("/deals", json={"name": "Project Falcon"}, headers=headers_a).json()

    # Tenant B sees an empty deal list and cannot address tenant A's objects by id.
    assert client.get("/deals", headers=headers_b).json() == []
    resp = client.get(f"/deals/{deal['id']}/documents", headers=headers_b)
    assert resp.status_code in (403, 404)


def test_tenants_cannot_see_each_others_documents_or_runs(client, tenant_factory):
    headers_a, _, _ = tenant_factory()
    headers_b, _, _ = tenant_factory()

    deal = client.post("/deals", json={"name": "Project Osprey"}, headers=headers_a).json()
    doc = client.post(
        f"/deals/{deal['id']}/documents",
        json={"filename": "cim.pdf", "content_type": "application/pdf"},
        headers=headers_a,
    ).json()
    run = client.post("/runs", json={"deal_id": deal["id"]}, headers=headers_a).json()

    assert client.get(f"/documents/{doc['id']}", headers=headers_b).status_code in (403, 404)
    assert client.get(f"/runs/{run['id']}", headers=headers_b).status_code in (403, 404)
    # And the owner can still see them.
    assert client.get(f"/runs/{run['id']}", headers=headers_a).status_code == 200


def test_unauthenticated_requests_rejected(client):
    assert client.get("/deals").status_code in (401, 403)
    assert client.get("/deals", headers={"Authorization": "Bearer bogus"}).status_code == 401
