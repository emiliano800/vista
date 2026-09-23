"""Company workspace imports and reviews run on the analyst's canonical contract and
write to the one ledger: an import approved by an employee is the import the analyst
sees, and a File Reviewer run started from the workspace lands its findings where both
sides read them."""

import base64
import uuid

from tests.conftest import requires_db
from tests.test_portfolio import SIMPLE, make_firm, source_store  # noqa: F401  (fixture re-export)
from vista.auth import token_digest
from vista.config import settings
from vista.db import platform_session, tenant_session
from vista.jobs.worker import process_one
from vista.models.platform import FirmCompany, Tenant, User
from vista.models.tenant import DealMembership

pytestmark = requires_db


def employee(cid: str, role: str = "member") -> tuple[dict, str]:
    """A user of the company's own tenant with a deal membership, as `manage add-user`
    creates one. Returns (headers, deal id)."""
    key = uuid.uuid4().hex + uuid.uuid4().hex
    with platform_session() as session:
        fc = session.get(FirmCompany, uuid.UUID(cid))
        tenant = session.get(Tenant, fc.tenant_id)
        user = User(
            tenant_id=tenant.id, email=f"emp-{uuid.uuid4().hex[:6]}@cedar.example.com", role="member", api_token_hash=token_digest(key)
        )
        session.add(user)
        session.commit()
        user_id, deal_id, schema = user.id, fc.deal_id, tenant.schema_name
    with tenant_session(schema) as ts:
        ts.add(DealMembership(deal_id=deal_id, user_id=user_id, role=role))
        ts.commit()
    return {"Authorization": f"Bearer {key}"}, str(deal_id)


def b64(name: str) -> str:
    return base64.b64encode((SIMPLE / name).read_bytes()).decode()


def test_company_import_writes_the_rows_the_analyst_reads(client, source_store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "demo_today", "2026-09-19")
    analyst, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=analyst, json={"name": "Cedar Climate"}).json()["id"]
    emp, deal = employee(cid)

    listing = client.get(f"/api/deals/{deal}/imports", headers=emp)
    assert listing.status_code == 200, listing.text
    assert listing.json()["role"] == "member" and listing.json()["imports"] == [] and listing.json()["company"]["id"] == cid
    assert "invoices" in client.get(f"/api/deals/{deal}/import-datasets", headers=emp).json()

    job = client.post(
        f"/api/deals/{deal}/imports", headers=emp, json={"name": "invoices.csv", "content": b64("invoices.csv"), "mime_type": "text/csv"}
    )
    assert job.status_code == 201, job.text
    job = job.json()
    assert job["dataset"] == "invoices" and job["status"] == "mapping_review" and job["recordsDetected"] == 3
    assert source_store.items, "the original file is kept as the source of record"
    assert client.get(f"/api/deals/{deal}/records", headers=emp).json()["invoices"] == [], "nothing canonical before approval"
    assert client.post(f"/api/deals/{deal}/imports/{job['id']}/approve", headers=emp).status_code == 409

    decisions = {"mappings": [{"source": m["source"], "target": m["target"], "confirmed": True} for m in job["mappings"]]}
    approved = client.post(f"/api/deals/{deal}/imports/{job['id']}/mappings/approve", headers=emp, json=decisions)
    assert approved.status_code == 200, approved.text
    assert client.get(f"/api/deals/{deal}/imports/{job['id']}/preview", headers=emp).status_code == 200
    done = client.post(f"/api/deals/{deal}/imports/{job['id']}/approve", headers=emp)
    assert done.status_code == 200 and done.json()["status"] == "completed" and done.json()["recordsImported"] == 3

    records = client.get(f"/api/deals/{deal}/records", headers=emp).json()
    assert len(records["invoices"]) == 3 and records["invoices"][0]["provenance"]["file"] == "invoices.csv"
    # The analyst reads the very same rows and the same import job.
    assert len(client.get(f"/api/companies/{cid}/invoices", headers=analyst).json()) == 3
    assert [j["status"] for j in client.get(f"/api/companies/{cid}/imports", headers=analyst).json()] == ["completed"]
    assert client.get(f"/api/companies/{cid}", headers=analyst).json()["metrics"]["invoiceCount"] == 3

    # A File Reviewer run started from the workspace is the analyst's reviewer, scoped to this Deal.
    while process_one():
        pass
    queued = client.post(f"/api/deals/{deal}/review", headers=emp)
    assert queued.status_code == 202, queued.text
    while process_one():
        pass
    run = client.get(f"/api/runs/{queued.json()['run_id']}", headers=emp).json()
    assert run["status"] == "succeeded" and run["deal_id"] == deal and run["run_type"] == "canonical_review"
    ours = [f for f in client.get(f"/api/findings?deal_id={deal}", headers=emp).json() if f["run_id"] == run["id"]]
    assert ours, "the reviewer wrote findings the company workspace lists"
    snap = client.get("/api/portfolio", headers=analyst).json()
    by_uuid = {f["uuid"]: f for f in snap["findings"]}
    assert {f["id"] for f in ours} <= set(by_uuid), "the analyst snapshot is the same ledger"
    assert all(by_uuid[f["id"]]["ref"] and by_uuid[f["id"]]["companyId"] == cid for f in ours)
    assert any(r["id"] == run["id"] and r["companyId"] == cid for r in snap["runs"])
    # Triage from the company side is what the analyst sees, and the other way round.
    assert client.patch(f"/api/findings/{ours[0]['id']}", headers=emp, json={"status": "reviewed"}).status_code == 200
    assert (
        next(f for f in client.get("/api/portfolio", headers=analyst).json()["findings"] if f["uuid"] == ours[0]["id"])["status"]
        == "Reviewed"
    )
    assert (
        client.post(f"/api/findings/{by_uuid[ours[0]['id']]['ref']}/status", headers=analyst, json={"status": "Dismissed"}).status_code
        == 200
    )
    assert client.get(f"/api/findings?deal_id={deal}&status=dismissed", headers=emp).json()[0]["id"] == ours[0]["id"]


def test_company_import_permissions_and_scope(client, source_store, tenant_factory):  # noqa: F811
    analyst, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=analyst, json={"name": "Cedar Climate"}).json()["id"]
    other_cid = client.post("/api/portfolio/companies", headers=analyst, json={"name": "Harbor Heating"}).json()["id"]
    viewer, deal = employee(cid, role="viewer")
    member, _ = employee(cid)
    _neighbour, other_deal = employee(other_cid)
    upload = {"name": "customers.csv", "content": b64("customers.csv"), "mime_type": "text/csv"}

    assert client.get(f"/api/deals/{deal}/imports", headers=viewer).json()["role"] == "viewer"
    assert client.post(f"/api/deals/{deal}/imports", headers=viewer, json=upload).status_code == 403
    assert client.post(f"/api/deals/{deal}/review", headers=viewer).status_code == 403
    assert client.get("/api/portfolio", headers=viewer).status_code == 403, "an employee has no analyst scope"

    # A member's job is addressed only under its own company's Deal.
    job = client.post(f"/api/deals/{deal}/imports", headers=member, json=upload).json()
    assert client.get(f"/api/deals/{deal}/imports/{job['id']}", headers=viewer).status_code == 200
    assert client.get(f"/api/deals/{other_deal}/imports/{job['id']}", headers=member).status_code == 403, "no membership on the sibling"
    assert client.get(f"/api/deals/{other_deal}/imports", headers=member).status_code == 403
    assert client.get(f"/api/deals/{uuid.uuid4()}/imports", headers=member).status_code == 403

    # A workspace whose tenant is not a portfolio company has nowhere canonical to import to.
    plain, _tenant_id, _user_id = tenant_factory()
    solo = client.post("/api/deals", headers=plain, json={"name": "Solo"}).json()["id"]
    assert client.get(f"/api/deals/{solo}/imports", headers=plain).status_code == 409
    assert client.get("/api/deals", headers=plain).status_code == 200, "the rest of the workspace still works"


def test_link_workspace_points_the_company_at_an_existing_employee_tenant(client, source_store, tenant_factory):  # noqa: F811
    """The live demo provisioned each company's workspace with create-workspace before the
    analyst's company existed, so the two lived in different tenants. link-workspace makes
    them one tenant: the employees' Deal becomes the company's deal_id and imports work."""
    from sqlalchemy import select

    from vista.models.platform import FirmCompany, Tenant
    from vista.portfolio.service import link_workspace

    analyst, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=analyst, json={"name": "Meridian Risk Partners, LLC"}).json()["id"]
    plain, tenant_id, _user_id = tenant_factory()
    deal = client.post("/api/deals", headers=plain, json={"name": "Meridian Risk Partners, LLC"}).json()["id"]
    assert client.get(f"/api/deals/{deal}/imports", headers=plain).status_code == 409, "not linked yet"

    with platform_session() as session:
        fc = session.get(FirmCompany, uuid.UUID(cid))
        tenant = session.get(Tenant, uuid.UUID(tenant_id))
        assert str(link_workspace(session, fc, tenant, uuid.UUID(deal))) == deal
        assert fc.tenant_id == tenant.id
        # A tenant belongs to one company; linking it to a second one is refused.
        other = session.scalar(select(FirmCompany).where(FirmCompany.id != fc.id, FirmCompany.firm_id == fc.firm_id))
    listing = client.get(f"/api/deals/{deal}/imports", headers=plain)
    assert listing.status_code == 200 and listing.json()["company"]["id"] == cid
    job = client.post(f"/api/deals/{deal}/imports", headers=plain, json={"name": "customers.csv", "content": b64("customers.csv")})
    assert job.status_code == 201, job.text
    assert [j["filename"] for j in client.get(f"/api/companies/{cid}/imports", headers=analyst).json()] == ["customers.csv"]
    assert other is None
    second = client.post("/api/portfolio/companies", headers=analyst, json={"name": "Harbor Heating"}).json()["id"]
    with platform_session() as session:
        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            link_workspace(session, session.get(FirmCompany, uuid.UUID(second)), session.get(Tenant, uuid.UUID(tenant_id)))
        assert refused.value.status_code == 409
