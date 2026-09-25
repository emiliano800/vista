"""PE analyst portfolio API: firm-scoped authorisation, tenant isolation, the
import pipeline (detect -> map -> preview -> approve -> canonical rows with
provenance) and the realized-value rule. Everything runs through the HTTP API."""

import base64
import uuid
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError

from tests.conftest import requires_db
from vista.auth import token_digest
from vista.config import settings
from vista.db import platform_session
from vista.models.platform import Firm, FirmMembership, User
from vista.portfolio.processors import get_processor
from vista.tenancy import provision_tenant

pytestmark = requires_db

SIMPLE = Path(__file__).resolve().parents[1] / "src" / "web" / "public" / "demo" / "simple"


def make_firm(role: str = "analyst"):
    """A firm with one member; returns (headers, firm_id)."""
    key = uuid.uuid4().hex + uuid.uuid4().hex
    tag = uuid.uuid4().hex[:8]
    home, _owner, _token = provision_tenant(f"Firm {tag}", f"owner@{tag}.example.com")
    with platform_session() as session:
        user = User(tenant_id=home.id, email=f"analyst@{tag}.example.com", role="member", api_token_hash=token_digest(key))
        firm = Firm(name=f"Firm {tag}", slug=f"firm-{tag}", home_tenant_id=home.id, synthetic_demo=True)
        session.add_all([user, firm])
        session.flush()
        session.add(FirmMembership(firm_id=firm.id, user_id=user.id, role=role, display_name="Test Analyst"))
        session.commit()
        return {"Authorization": f"Bearer {key}"}, str(firm.id)


@pytest.fixture()
def source_store(monkeypatch):
    class Store:
        def __init__(self):
            self.items = {}
            self.fail = False

        def put_object(self, **kwargs):
            if self.fail:
                raise EndpointConnectionError(endpoint_url="http://unavailable")
            self.items[kwargs["Key"]] = kwargs["Body"]

    store = Store()
    monkeypatch.setattr("vista.portfolio.imports.s3_client", lambda: store)
    return store


def upload(client, headers, company_id, name: str, text: str | None = None):
    content = (SIMPLE / name).read_bytes() if text is None else text.encode()
    resp = client.post(
        f"/api/companies/{company_id}/imports",
        headers=headers,
        json={"name": name, "content": base64.b64encode(content).decode(), "mime_type": "text/csv"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_simple_sample_csvs_detect_with_high_confidence():
    processor = get_processor("demo")
    expected = {
        "customers.csv": "customers",
        "invoices.csv": "invoices",
        "vendor_purchases.csv": "vendors",
        "software.csv": "subscriptions",
    }
    for name, dataset in expected.items():
        columns = (SIMPLE / name).read_text().splitlines()[0].split(",")
        got, confidence = processor.detect_dataset(columns, name)
        assert got == dataset, name
        assert confidence >= 0.9, (name, confidence)


def test_only_firm_members_reach_the_portfolio(client, tenant_factory):
    plain_headers, _, _ = tenant_factory()  # a workspace user with no firm membership
    assert client.get("/api/portfolio/me").status_code == 401
    assert client.get("/api/portfolio/me", headers=plain_headers).status_code == 403
    headers, firm_id = make_firm()
    me = client.get("/api/portfolio/me", headers=headers).json()
    assert me["firm"]["id"] == firm_id
    assert me["role"] == "analyst"
    assert client.get("/api/portfolio", headers=headers).json()["companies"] == []


def test_firms_cannot_see_each_others_companies_and_viewers_cannot_write(client, source_store):
    headers_a, _ = make_firm()
    headers_b, _ = make_firm()
    viewer, _ = make_firm(role="viewer")

    company = client.post("/api/portfolio/companies", headers=headers_a, json={"name": "Cedar Climate", "acquired": "2026-08-01"})
    assert company.status_code == 201, company.text
    cid = company.json()["id"]
    assert company.json()["status"] == "onboarding"
    job = upload(client, headers_a, cid, "customers.csv")

    for path in (f"/api/companies/{cid}", f"/api/companies/{cid}/customers", f"/api/import-jobs/{job['id']}"):
        assert client.get(path, headers=headers_b).status_code == 404, path
    assert client.get("/api/portfolio", headers=headers_b).json()["companies"] == []
    assert client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers_b).status_code == 404
    sneak = {"title": "Sneak", "companyId": cid, "category": "Integration"}
    assert client.post("/api/tasks", headers=headers_b, json=sneak).status_code == 404

    assert client.post("/api/portfolio/companies", headers=viewer, json={"name": "Nope"}).status_code == 403
    assert client.post("/api/portfolio/analysis", headers=viewer).status_code == 403


def test_import_pipeline_writes_canonical_rows_only_after_approval(client, source_store, monkeypatch):
    monkeypatch.setattr(settings, "demo_today", "2026-09-19")  # the simple fixture's invoices are dated mid-2026
    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]

    job = upload(client, headers, cid, "invoices.csv")
    assert job["dataset"] == "invoices"
    assert job["detection"]["confidence"] >= 0.9
    assert job["recordsDetected"] == 3
    assert source_store.items, "the raw file is kept as the source of record"
    mappings = job["mappings"]
    targets = {m["target"] for m in mappings}
    assert targets >= {"source_invoice_number", "customer_name", "issue_date", "due_date", "amount", "outstanding_balance", "status"}
    assert all(m["status"] == "Ready" for m in mappings)

    # Nothing canonical exists before approval, and approval needs approved mappings first.
    assert client.get(f"/api/companies/{cid}/invoices", headers=headers).json() == []
    assert client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers).status_code == 409

    approved = client.post(
        f"/api/import-jobs/{job['id']}/mappings/approve",
        headers=headers,
        json={"mappings": [{"source": m["source"], "target": m["target"], "confirmed": True} for m in mappings]},
    )
    assert approved.status_code == 200, approved.text
    preview = client.get(f"/api/import-jobs/{job['id']}/preview", headers=headers).json()
    changes = {(p["field"], p["source"]): p["normalized"] for p in preview}
    assert changes[("amount", "1850.00")] == "1850.0"
    assert changes[("status", "Paid")] == "paid"
    assert client.get(f"/api/companies/{cid}/invoices", headers=headers).json() == [], "preview does not write"

    done = client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"
    assert done.json()["recordsImported"] == 3

    invoices = client.get(f"/api/companies/{cid}/invoices", headers=headers).json()
    assert len(invoices) == 3
    overdue = next(i for i in invoices if i["number"] == "INV-1003")
    assert overdue["outstanding"] == 960.5
    assert overdue["provenance"]["file"] == "invoices.csv"
    assert overdue["provenance"]["importJob"] == job["id"]
    assert overdue["provenance"]["row"] == 4, "spreadsheet row number, header is row 1"
    assert "original" not in overdue["provenance"], "list rows carry the provenance summary; values come per record"
    detail = client.get(f"/api/companies/{cid}/records/invoices/{overdue['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["provenance"]["original"]["Amount Due"] == "960.50"
    assert detail.json()["provenance"]["normalized"]["outstanding_balance"] == 960.5
    full = client.get(f"/api/companies/{cid}/invoices?provenance=full", headers=headers).json()
    assert next(i for i in full if i["number"] == "INV-1003")["provenance"]["original"]["Amount Due"] == "960.50"
    assert client.get(f"/api/companies/{cid}/records/widgets/{overdue['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/companies/{cid}/records/invoices/{uuid.uuid4()}", headers=headers).status_code == 404

    company = client.get(f"/api/companies/{cid}", headers=headers).json()
    assert company["metrics"]["outstandingAr"] == pytest.approx(1850.0 + 960.5)
    assert company["metrics"]["overdueAr"] == pytest.approx(1850.0 + 960.5), "demo today is 2026-09-19; both open invoices are past due"

    # Re-approving is idempotent: no duplicate rows, mappings are locked.
    assert client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers).status_code == 200
    assert client.post(f"/api/import-jobs/{job['id']}/mappings/approve", headers=headers, json={"mappings": []}).status_code == 409
    assert len(client.get(f"/api/companies/{cid}/invoices", headers=headers).json()) == 3


def test_ambiguous_columns_need_review_and_dataset_can_be_overridden(client, source_store):
    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]
    job = upload(client, headers, cid, "export.csv", "Name,Amount,Date\nBayside Dental,100,2026-01-01\n")
    assert job["detection"]["confidence"] < 0.9
    assert any(m["status"] == "Review" or m["target"] is None for m in job["mappings"])

    changed = client.post(f"/api/import-jobs/{job['id']}/dataset", headers=headers, json={"dataset": "customers"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["dataset"] == "customers"
    assert client.post(f"/api/import-jobs/{job['id']}/dataset", headers=headers, json={"dataset": "payroll"}).status_code == 422

    # Required fields must be mapped before the job can move on.
    bare = upload(client, headers, cid, "customers.csv", "Email,Phone\na@b.com,617-555-0100\n")
    rejected = client.post(f"/api/import-jobs/{bare['id']}/mappings/approve", headers=headers, json={"mappings": []})
    assert rejected.status_code == 422
    assert "required" in rejected.json()["detail"].lower()
    assert client.post(f"/api/import-jobs/{bare['id']}/approve", headers=headers).status_code == 409


def test_source_storage_failure_saves_nothing(client, source_store):
    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]
    source_store.fail = True
    resp = client.post(
        f"/api/companies/{cid}/imports",
        headers=headers,
        json={"name": "customers.csv", "content": base64.b64encode(b"Customer Name\nA\n").decode()},
    )
    assert resp.status_code == 503
    assert client.get(f"/api/companies/{cid}/imports", headers=headers).json() == []


def test_realized_value_requires_an_implemented_completed_task(client):
    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]
    task = client.post(
        "/api/tasks",
        headers=headers,
        json={"title": "Renegotiate capacitor pricing", "companyId": cid, "category": "Opportunity follow-up", "priority": "High"},
    )
    assert task.status_code == 201, task.text
    ref = task.json()["id"]
    assert task.json()["status"] == "Open"

    patch = {"status": "Complete", "outcome": "Benefit validated", "realizedResult": 999}
    validated = client.post(f"/api/tasks/{ref}", headers=headers, json=patch).json()
    assert validated["status"] == "Complete"
    assert validated["realizedResult"] is None, "only an Implemented outcome may carry a realized amount"
    assert client.post(f"/api/tasks/{ref}", headers=headers, json={"status": "Bogus"}).status_code == 422
    assert client.post("/api/opportunities/OP-999999/status", headers=headers, json={"status": "Dismissed"}).status_code == 404


def test_new_company_gets_one_deal_that_both_views_resolve_by_id(client):
    """The analyst's company and the employee-facing surfaces (company workspace, recorder)
    must point at the same rows: creating a company creates its Deal and records the id."""
    from sqlalchemy import select

    from vista.db import tenant_session
    from vista.models.platform import FirmCompany, Tenant
    from vista.models.tenant import Deal
    from vista.portfolio.service import ensure_company_deal

    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]
    with platform_session() as session:
        fc = session.get(FirmCompany, uuid.UUID(cid))
        schema = session.get(Tenant, fc.tenant_id).schema_name
        assert fc.deal_id is not None
        with tenant_session(schema) as ts:
            deals = ts.scalars(select(Deal)).all()
            assert [d.id for d in deals] == [fc.deal_id] and deals[0].name == "Cedar Climate"
        # Idempotent: resolving again reuses the same Deal rather than creating a second one.
        assert ensure_company_deal(session, fc, schema, fc.deal_id) == fc.deal_id
        with tenant_session(schema) as ts:
            assert len(ts.scalars(select(Deal)).all()) == 1


def test_published_recorder_reports_reach_the_analyst_company_page(client):
    """The recorder's reports live in the company tenant beside the findings. The analyst
    sees exactly the published ones for that company (by company id or by its Deal); drafts
    and other workspaces stay invisible, and another firm gets nothing."""
    from datetime import UTC, datetime

    from vista.db import tenant_session
    from vista.models.platform import FirmCompany, Tenant
    from vista.models.tenant import RecorderReport, RecorderSubmission

    headers, _ = make_firm()
    other, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]
    with platform_session() as session:
        fc = session.get(FirmCompany, uuid.UUID(cid))
        schema, deal_id = session.get(Tenant, fc.tenant_id).schema_name, str(fc.deal_id)
    employee = uuid.uuid4()

    def seed(ts, workspace, status, summary):
        sub = RecorderSubmission(
            uploaded_by=employee,
            device_id=uuid.uuid4(),
            source_id=uuid.uuid4().hex,
            manifest={"workspace": workspace},
            manifest_hash=uuid.uuid4().hex * 2,
            upload_status="accepted",
        )
        ts.add(sub)
        ts.flush()
        report = RecorderReport(
            submission_id=sub.id,
            uploaded_by=employee,
            workspace=workspace,
            status=status,
            observed={"session": {"started_at": "2026-09-20T09:00:00Z"}, "apps": [{"app": "QuickBooks"}], "switches": 4},
            interpretation={"summary": summary, "workflows": [{"name": "AP entry"}]},
            questions=[{"question": "Is this daily?", "answer": "Yes"}],
            published_at=datetime.now(UTC) if status == "published" else None,
        )
        ts.add(report)
        ts.flush()
        return str(report.id)

    with tenant_session(schema) as ts:
        by_deal = seed(ts, {"kind": "deal", "id": deal_id}, "published", "Re-keys invoices from email into QuickBooks")
        by_company = seed(ts, {"kind": "company", "id": cid}, "published", "Copies PO numbers by hand")
        draft = seed(ts, {"kind": "deal", "id": deal_id}, "draft", "Not yet shared")
        elsewhere = seed(ts, {"kind": "deal", "id": str(uuid.uuid4())}, "published", "Another workspace")
        ts.commit()

    listed = client.get(f"/api/companies/{cid}/reports", headers=headers)
    assert listed.status_code == 200, listed.text
    assert {r["id"] for r in listed.json()} == {by_deal, by_company}
    assert all("observed" not in r and r["status"] == "published" for r in listed.json()), "list view is the summary shape"
    full = client.get(f"/api/companies/{cid}/reports/{by_deal}", headers=headers).json()
    assert full["observed"]["apps"][0]["app"] == "QuickBooks" and full["questions"][0]["answer"] == "Yes"
    assert full["summary"] == "Re-keys invoices from email into QuickBooks" and full["workflows"] == 1
    for hidden in (draft, elsewhere):
        assert client.get(f"/api/companies/{cid}/reports/{hidden}", headers=headers).status_code == 404
    assert client.get(f"/api/companies/{cid}/reports", headers=other).status_code == 404
    assert client.get(f"/api/companies/{cid}/reports/{by_deal}", headers=other).status_code == 404


def test_next_ref_rereads_the_counter_under_the_lock():
    """A firm already loaded in the session must not hand out a ref that another
    transaction allocated meanwhile (the production OP-063 duplicate)."""
    from vista.portfolio.service import next_ref

    _headers, firm_id = make_firm()
    fid = uuid.UUID(firm_id)
    with platform_session() as mine:
        firm = mine.get(Firm, fid)  # cached in the identity map, counters == {}
        assert firm.counters in ({}, None)
        with platform_session() as other:
            first = next_ref(other, fid, "opportunity", "OP", 3)
            other.commit()
        second = next_ref(mine, fid, "opportunity", "OP", 3)
        mine.commit()
    assert first == "OP-001"
    assert second == "OP-002"


def test_unreadable_amount_is_an_exception_not_a_silent_zero(client, source_store, monkeypatch):
    monkeypatch.setattr(settings, "demo_today", "2026-09-19")
    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate"}).json()["id"]
    csv = (SIMPLE / "invoices.csv").read_text().replace("960.50", "N/A", 1)
    assert "N/A" in csv

    def run(decision: str | None):
        job = upload(client, headers, cid, "invoices.csv", csv)
        mappings = [{"source": m["source"], "target": m["target"], "confirmed": True} for m in job["mappings"]]
        approved = client.post(f"/api/import-jobs/{job['id']}/mappings/approve", headers=headers, json={"mappings": mappings})
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "validating"
        xs = [x for x in approved.json()["exceptions"] if x["type"] == "Unreadable amount"]
        assert len(xs) == 1 and "N/A" in xs[0]["description"] and xs[0]["actions"] == ["Skip row", "Import as zero"]
        preview = client.get(f"/api/import-jobs/{job['id']}/preview", headers=headers).json()
        assert any(p["source"] == "N/A" and p["normalized"] == "unreadable" for p in preview)
        if decision:
            r = client.post(f"/api/companies/{cid}/exceptions/{xs[0]['uuid']}/resolve", headers=headers, json={"decision": decision})
            assert r.status_code == 200, r.text
        done = client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers)
        assert done.status_code == 200, done.text
        return done.json()

    # Undecided: the row is rejected and reported, and no zero reaches the ledger.
    undecided = run(None)
    assert (undecided["recordsImported"], undecided["recordsRejected"]) == (2, 1)
    invoices = client.get(f"/api/companies/{cid}/invoices", headers=headers).json()
    assert {i["number"] for i in invoices} == {"INV-1001", "INV-1002"}
    assert all(i["outstanding"] != 0 or i["status"] == "paid" for i in invoices)

    # Decided as zero: the row imports with the original text kept in provenance.
    zeroed = run("Import as zero")
    assert (zeroed["recordsImported"], zeroed["recordsRejected"]) == (3, 0)
    inv = next(i for i in client.get(f"/api/companies/{cid}/invoices?provenance=full", headers=headers).json() if i["number"] == "INV-1003")
    assert inv["amount"] == 0 and inv["provenance"]["original"]["Invoice Total"] == "N/A"


def test_normalize_money_reads_numbers_and_refuses_text():
    from vista.portfolio.processors import normalize_money

    assert normalize_money("$1,850.50") == 1850.5
    assert normalize_money("(120.00)") == -120.0
    assert normalize_money("-45") == -45.0
    assert normalize_money("12.5%") == 12.5
    assert normalize_money("1,200 USD") == 1200.0
    assert normalize_money(42) == 42.0
    assert normalize_money("") == 0.0 and normalize_money(None) == 0.0
    assert normalize_money("N/A") is None and normalize_money("TBD") is None and normalize_money("see note") is None


def test_light_snapshot_carries_record_summaries_and_answers_304_when_unchanged(client, source_store, monkeypatch):
    monkeypatch.setattr(settings, "demo_today", "2026-09-19")  # the simple fixture's invoices are dated mid-2026
    headers, _ = make_firm()
    cid = client.post("/api/portfolio/companies", headers=headers, json={"name": "Cedar Climate", "acquired": "2026-08-01"}).json()["id"]
    job = upload(client, headers, cid, "invoices.csv")
    client.post(
        f"/api/import-jobs/{job['id']}/mappings/approve",
        headers=headers,
        json={"mappings": [{"source": m["source"], "target": m["target"], "confirmed": True} for m in job["mappings"]]},
    )
    assert client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers).status_code == 200

    light = client.get("/api/portfolio?records=false", headers=headers)
    assert light.status_code == 200, light.text
    etag = light.headers["etag"]
    assert etag.startswith('W/"')
    company = light.json()["companies"][0]
    assert "invoices" not in company, "the light snapshot leaves the rows out"
    assert company["records"]["invoices"] == {
        "count": 3,
        "files": ["invoices.csv"],
        "autoAccepted": 3,
        "reviewed": 0,
        "humanReviewed": 0,
    }
    assert company["records"]["customers"]["count"] == 0
    assert company["metrics"]["invoiceCount"] == 3

    again = client.get("/api/portfolio?records=false", headers={**headers, "If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag

    # A change on the ledger changes the validator: the client gets a fresh body.
    task = {"title": "Chase INV-1003", "companyId": cid, "category": "Integration"}
    assert client.post("/api/tasks", headers=headers, json=task).status_code == 201
    changed = client.get("/api/portfolio?records=false", headers={**headers, "If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag
    assert len(changed.json()["tasks"]) == 1

    full = client.get("/api/portfolio", headers=headers).json()["companies"][0]
    assert len(full["invoices"]) == 3
    assert set(full["invoices"][0]["provenance"]) == {"file", "sheet", "row", "importJob", "confidence", "review"}
