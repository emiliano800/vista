"""The two-layer pipeline over the canonical `synthetic_data/` tree.

Fact layer: real synthetic files (high-tier CSV, low-tier legacy workbook) go through
the import contract into policies / purchase orders / PO lines / inventory with
row-level provenance, idempotently. Interpretation layer: `run_portfolio_interpretation`
queues durable jobs only (stable idempotency keys, parent-run lineage, handoff
events); the worker executes them and the results reach `GET /api/portfolio`."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from tests.conftest import requires_db
from tests.test_portfolio import make_firm, source_store  # noqa: F401  (fixture re-export)
from vista.auth import principal_for_key
from vista.db import platform_session, tenant_session
from vista.jobs.worker import process_one
from vista.models.platform import FirmCompany, Job, Tenant
from vista.models.tenant import AgentRun, AgentRunEvent, Finding, InventoryBalance, PurchaseOrder, PurchaseOrderLine
from vista.portfolio import imports, interpret
from vista.portfolio.access import load_firm_context
from vista.portfolio.processors import get_processor, workbook_sheets

pytestmark = requires_db

SYNTHETIC = Path(__file__).resolve().parents[1] / "synthetic_data"
NORTHFIELD = SYNTHETIC / "industrial_goods" / "northfield_industrial_components"
KEYSTONE = SYNTHETIC / "industrial_goods" / "keystone_bearing_and_drive"
MERIDIAN = SYNTHETIC / "insurance_broking" / "meridian_risk_partners"
CASTLEBROOK = SYNTHETIC / "insurance_broking" / "castlebrook_agency"


def _ctx(platform, headers):
    return load_firm_context(platform, principal_for_key(headers["Authorization"].split()[1]))


def import_table(client, headers, cid, path: Path, dataset: str, sheet: str | None = None) -> dict:
    """Stand in for the analyst at both gates: confirm every deterministic mapping at or
    above the auto threshold, leave the rest unmapped, then approve the import."""
    processor = get_processor("demo")
    with platform_session() as platform:
        ctx = _ctx(platform, headers)
        ref = ctx.company(cid)
        job = imports.create_import(platform, ctx, ref, path.name, path.read_bytes(), "", processor=processor, dataset=dataset, sheet=sheet)
        job_id = job.id
    view = client.get(f"/api/import-jobs/{job_id}", headers=headers).json()
    decisions = [
        {"source": m["source"], "target": m["target"] if m["status"] == "Ready" else None, "confirmed": m["status"] == "Ready"}
        for m in view["mappings"]
    ]
    approved = client.post(f"/api/import-jobs/{job_id}/mappings/approve", headers=headers, json={"mappings": decisions})
    assert approved.status_code == 200, approved.text
    done = client.post(f"/api/import-jobs/{job_id}/approve", headers=headers)
    assert done.status_code == 200, done.text
    return done.json()


def new_company(client, headers, name: str, industry: str) -> str:
    resp = client.post(
        "/api/portfolio/companies",
        headers=headers,
        json={"name": name, "industry": industry, "location": "Test, TN", "acquired": "2024-01-01"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_high_tier_industrial_files_land_as_purchase_orders_lines_and_inventory(client, source_store):  # noqa: F811
    headers, _ = make_firm()
    cid = new_company(client, headers, "Northfield Industrial Components", "Industrial distribution")
    proc = NORTHFIELD / "03_procurement"
    import_table(client, headers, cid, proc / "suppliers.csv", "vendor_master")
    pos = import_table(client, headers, cid, proc / "purchase_orders.csv", "purchase_orders")
    lines = import_table(client, headers, cid, proc / "purchase_order_lines.csv", "purchase_order_lines")
    inv = import_table(client, headers, cid, NORTHFIELD / "05_inventory" / "inventory_balances.csv", "inventory")
    assert (pos["recordsImported"], lines["recordsImported"], inv["recordsImported"]) == (260, 665, 127)

    body = client.get(f"/api/companies/{cid}/purchase-orders", headers=headers).json()
    assert len(body["purchaseOrders"]) == 260 and len(body["lines"]) == 665
    po = next(p for p in body["purchaseOrders"] if p["poNumber"] == "PO-31001")
    assert po["vendorId"], "PO resolved to the imported supplier row"
    assert po["lineCount"] == sum(1 for line in body["lines"] if line["poNumber"] == "PO-31001") > 0
    assert po["provenance"]["file"] == "purchase_orders.csv" and po["provenance"]["row"] >= 2
    assert set(po["provenance"]["original"]) >= {"po_number", "supplier_name"}
    line = next(line for line in body["lines"] if line["poNumber"] == "PO-31001" and line["lineNumber"] == 1)
    assert line["purchaseOrderId"] == po["id"]
    assert (line["orderedQty"], line["unitCost"], line["extendedCost"]) == (50, 21.4, 1070.0)

    inventory = client.get(f"/api/companies/{cid}/inventory", headers=headers).json()
    assert len(inventory) == 127
    assert all(b["provenance"]["file"] == "inventory_balances.csv" for b in inventory)
    assert client.get(f"/api/companies/{cid}/policies", headers=headers).json() == []

    company = client.get(f"/api/companies/{cid}", headers=headers).json()
    m = company["metrics"]
    assert m["purchaseOrderCount"] == 260 and m["purchaseOrderSpend"] > 0
    assert m["openPoLines"] == sum(1 for line in body["lines"] if line["receivedQty"] < line["orderedQty"])
    assert m["inventoryItems"] > 0 and m["inventoryValue"] > 0
    assert m["policyCount"] == 0
    assert {s["key"]: s["status"] for s in company["integration"]["steps"]}["operations"] == "Complete"

    # Re-approving the completed job writes nothing new.
    with tenant_session(company_schema(cid)) as ts:
        before = {t: ts.scalar(select(func.count()).select_from(t)) for t in (PurchaseOrder, PurchaseOrderLine, InventoryBalance)}
    for job in client.get(f"/api/companies/{cid}/imports", headers=headers).json():
        assert client.post(f"/api/import-jobs/{job['id']}/approve", headers=headers).status_code == 200
    with tenant_session(company_schema(cid)) as ts:
        after = {t: ts.scalar(select(func.count()).select_from(t)) for t in (PurchaseOrder, PurchaseOrderLine, InventoryBalance)}
    assert after == before


def company_schema(cid: str) -> str:
    with platform_session() as p:
        fc = p.get(FirmCompany, uuid.UUID(cid))
        return p.get(Tenant, fc.tenant_id).schema_name


def test_medium_tier_workbook_sheet_imports_inventory(client, source_store):  # noqa: F811
    headers, _ = make_firm()
    cid = new_company(client, headers, "Keystone Bearing & Drive", "Industrial distribution")
    path = KEYSTONE / "05_inventory" / "inventory_balances.xlsx"
    sheet = workbook_sheets(path.read_bytes())[0]
    done = import_table(client, headers, cid, path, "inventory", sheet=sheet)
    assert done["recordsImported"] == 97
    rows = client.get(f"/api/companies/{cid}/inventory", headers=headers).json()
    prov = rows[0]["provenance"]
    assert (prov["file"], prov["sheet"]) == ("inventory_balances.xlsx", sheet)
    # Mixed date formats in the workbook are normalised; the raw cell text is preserved.
    dated = [r for r in rows if r["lastIssueDate"]]
    assert dated and all(len(r["lastIssueDate"]) == 10 for r in dated)


def test_policies_join_to_imported_clients(client, source_store):  # noqa: F811
    headers, _ = make_firm()
    cid = new_company(client, headers, "Meridian Risk Partners", "Insurance Brokerage (Commercial P&C)")
    import_table(client, headers, cid, MERIDIAN / "01_clients_crm" / "clients.csv", "customers")
    done = import_table(client, headers, cid, MERIDIAN / "02_policies_exposures" / "policies.csv", "policies")
    assert done["recordsImported"] == 122
    policies = client.get(f"/api/companies/{cid}/policies", headers=headers).json()
    assert len(policies) == 122
    p = next(p for p in policies if p["policyNumber"] == "CNA-GL-9800363")
    assert p["customerId"], "policy resolved to the imported client row"
    assert (p["annualPremium"], p["commissionPct"], p["status"], p["surplusLines"]) == (55435.0, 15.0, "in_force", False)
    assert p["provenance"]["original"]["policy_status"] == "In Force"
    m = client.get(f"/api/companies/{cid}", headers=headers).json()["metrics"]
    assert m["policyCount"] == 122 and m["policyPremium"] == pytest.approx(5625033.0)
    assert m["policiesExpiring90"] == sum(
        1 for q in policies if q["expirationDate"] and "2026-03-31" <= q["expirationDate"] <= "2026-06-29"
    )


def test_low_tier_workbook_leaves_ambiguous_required_columns_at_the_mapping_gate(client, source_store):  # noqa: F811
    headers, _ = make_firm()
    cid = new_company(client, headers, "Castlebrook Agency", "Insurance agency")
    path = CASTLEBROOK / "00_legacy_exports" / "MASTER_WORKBOOK_v7_FINAL (2).xlsx"
    sheets = workbook_sheets(path.read_bytes())
    assert sheets, "the legacy export is a multi-sheet workbook"
    with platform_session() as platform:
        ctx = _ctx(platform, headers)
        ref = ctx.company(cid)
        for sheet in sheets:
            job = imports.create_import(platform, ctx, ref, path.name, path.read_bytes(), "", processor=get_processor("demo"), sheet=sheet)
            view = client.get(f"/api/import-jobs/{job.id}", headers=headers).json()
            assert view["sheet"] == sheet
            # Nothing below the auto threshold is confirmed by anyone but the analyst.
            weak = [m for m in view["mappings"] if m["status"] != "Ready"]
            for m in weak:
                assert m["confidence"] < 0.9
    jobs = client.get(f"/api/companies/{cid}/imports", headers=headers).json()
    assert len(jobs) == len(sheets)
    assert all(j["status"] in ("mapping_review", "detected", "mapped") for j in jobs)
    assert client.get(f"/api/companies/{cid}/policies", headers=headers).json() == []


def test_interpretation_is_queued_with_lineage_and_executed_by_the_worker(client, source_store, monkeypatch):  # noqa: F811
    headers, firm_id = make_firm()
    other, _ = make_firm()
    a = new_company(client, headers, "Northfield Industrial Components", "Industrial distribution")
    b = new_company(client, headers, "Keystone Bearing & Drive", "Industrial distribution")
    for cid, root in ((a, NORTHFIELD), (b, KEYSTONE)):
        import_table(client, headers, cid, root / "03_procurement" / "suppliers.csv", "vendor_master")
        import_table(client, headers, cid, root / "03_procurement" / "purchase_orders.csv", "purchase_orders")
        import_table(client, headers, cid, root / "03_procurement" / "purchase_order_lines.csv", "purchase_order_lines")

    first = client.post("/api/portfolio/interpretation", headers=headers)
    assert first.status_code == 202, first.text
    report = first.json()
    assert set(report["review"]) == {a, b} and set(report["merge"]) == {"industrial_goods"}
    request_id = uuid.UUID(report["request_id"])

    # Same request => same jobs (stable idempotency keys); nothing has run yet.
    with platform_session() as platform:
        ctx = _ctx(platform, headers)
        again = interpret.run_portfolio_interpretation(platform, ctx, request_id)
        platform.commit()
    assert again == report
    with platform_session() as platform:
        jobs = {str(j.id): j for j in platform.scalars(select(Job).where(Job.kind.in_(list(interpret.HANDLERS))))}
        for jid in (*report["review"].values(), *report["merge"].values()):
            assert jobs[jid].status == "queued"
        merge_payload = jobs[report["merge"]["industrial_goods"]].payload
        assert set(merge_payload["company_ids"]) == {a, b}
    with tenant_session(company_schema(a)) as ts:
        review_run = ts.scalar(select(AgentRun).where(AgentRun.run_type == interpret.RUN_REVIEW).order_by(AgentRun.created_at.desc()))
        assert review_run.status == "queued"
        handoff = ts.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == review_run.id, AgentRunEvent.event_type == "handoff"))
        assert handoff.data["to"] == interpret.RUN_MERGE
        assert handoff.data["job_id"] == report["merge"]["industrial_goods"]
        assert handoff.data["run_id"] == merge_payload["run_id"]
        assert str(review_run.id) in merge_payload["parent_run_ids"]
    assert client.get("/api/portfolio", headers=headers).json()["findings"] == []

    # The worker path executes the chain (stub model: no live key in tests).
    while process_one():
        pass
    with platform_session() as platform:
        for jid in (*report["review"].values(), *report["merge"].values()):
            job = platform.get(Job, uuid.UUID(jid))
            assert job.status == "succeeded", (job.kind, job.error)
    with tenant_session(company_schema(a)) as ts:
        run = ts.get(AgentRun, review_run.id)
        assert run.status == "succeeded"
        assert ts.scalar(select(func.count()).select_from(Finding).where(Finding.company == run.company)) > 0
        started = ts.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id, AgentRunEvent.data["message"].astext == "started"))
        assert started is not None

    snap = client.get("/api/portfolio", headers=headers).json()
    assert snap["findings"], "interpretation findings reach the analyst workspace through the API"
    assert all(f["companyId"] in (a, b) for f in snap["findings"])
    for c in snap["companies"]:
        assert c["analysisRunAt"]
        assert {s["key"]: s["status"] for s in c["integration"]["steps"]}["analysis"] == "Complete"
    # Cross-company opportunities cite canonical record ids only from firm-scoped companies.
    for opp in snap["opportunities"]:
        assert set(opp["companyIds"]) <= {a, b}

    # Another firm sees none of it.
    other_snap = client.get("/api/portfolio", headers=other).json()
    assert other_snap["companies"] == [] and other_snap["findings"] == [] and other_snap["opportunities"] == []

    # A second request re-runs idempotently: findings are updated in place, not duplicated.
    with tenant_session(company_schema(a)) as ts:
        n_before = ts.scalar(select(func.count()).select_from(Finding))
    assert client.post("/api/portfolio/interpretation", headers=headers).status_code == 202
    while process_one():
        pass
    with tenant_session(company_schema(a)) as ts:
        assert ts.scalar(select(func.count()).select_from(Finding)) == n_before
