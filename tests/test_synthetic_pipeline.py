"""The two-layer pipeline over the canonical `synthetic_data/` tree.

Fact layer: real synthetic files (high-tier CSV, low-tier legacy workbook) go through
the import contract into policies / purchase orders / PO lines / inventory with
row-level provenance, idempotently. Interpretation layer (hybrid): `run_portfolio_interpretation`
queues one File Reviewer job per company (stable idempotency keys) and a Sector Merger envelope
per sector whose job is created by the terminal barrier once every review in the sector has
succeeded or permanently failed; the merger recomputes deterministic candidates and validates
them against structured reviewer findings (BLOCK / DEGRADE / ENRICH) before the model explains
them. Results reach `GET /api/portfolio` with finding/run lineage."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from tests.conftest import requires_db
from tests.test_portfolio import make_firm, source_store  # noqa: F401  (fixture re-export)
from vista.auth import principal_for_key
from vista.db import platform_session, tenant_session
from vista.jobs.worker import process_one
from vista.models.platform import Firm, FirmCompany, Job, Opportunity, Tenant
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


def merge_jobs(platform, request_id) -> list[Job]:
    return platform.scalars(select(Job).where(Job.kind == interpret.RUN_MERGE, Job.idempotency_key.like(f"%:{request_id}:%"))).all()


def home_schema(firm_id) -> str:
    with platform_session() as p:
        return p.get(Tenant, p.get(Firm, firm_id).home_tenant_id).schema_name


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

    # The merge job does not exist yet: the barrier creates it once every review in the sector is terminal.
    assert report["merge"]["industrial_goods"] is None

    # Same request => same jobs (stable idempotency keys); nothing has run yet.
    with platform_session() as platform:
        ctx = _ctx(platform, headers)
        again = interpret.run_portfolio_interpretation(platform, ctx, request_id)
        platform.commit()
    assert again == report
    with platform_session() as platform:
        jobs = {str(j.id): j for j in platform.scalars(select(Job).where(Job.kind.in_(list(interpret.HANDLERS))))}
        for jid in report["review"].values():
            assert jobs[jid].status == "queued"
        assert merge_jobs(platform, request_id) == []
        merge_run_id = jobs[report["review"][a]].payload["merge_run_id"]
        assert merge_run_id and jobs[report["review"][b]].payload["merge_run_id"] == merge_run_id
    with tenant_session(company_schema(a)) as ts:
        review_run = ts.scalar(select(AgentRun).where(AgentRun.run_type == interpret.RUN_REVIEW).order_by(AgentRun.created_at.desc()))
        assert review_run.status == "queued"
        assert ts.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == review_run.id, AgentRunEvent.event_type == "handoff")) is None
    assert client.get("/api/portfolio", headers=headers).json()["findings"] == []

    # The analyst UI polls the request until every hop is terminal; the merge shows as waiting on its reviewers.
    status = client.get(f"/api/portfolio/interpretation/{request_id}", headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["done"] is False and status.json()["succeeded"] == 0
    waiting = [j for j in status.json()["jobs"] if j["kind"] == interpret.RUN_MERGE]
    assert len(waiting) == 1
    assert {k: v for k, v in waiting[0].items() if k != "waiting_on"} == {
        "id": None,
        "kind": interpret.RUN_MERGE,
        "scope": "industrial_goods",
        "status": interpret.MERGE_WAITING,
        "attempts": 0,
        "error": None,
    }
    assert set(waiting[0]["waiting_on"]) == {a, b}
    assert client.get(f"/api/portfolio/interpretation/{request_id}", headers=other).status_code == 404
    assert client.get(f"/api/portfolio/interpretation/{uuid.uuid4()}", headers=headers).status_code == 404

    # One review done: barrier still closed (the other review is not terminal).
    assert process_one()
    with platform_session() as platform:
        ctx = _ctx(platform, headers)
        assert interpret.release_sector_barrier(platform, ctx, str(request_id), "industrial_goods") is None
        assert merge_jobs(platform, request_id) == []
    status = client.get(f"/api/portfolio/interpretation/{request_id}", headers=headers).json()
    assert status["done"] is False and status["succeeded"] == 1
    assert [j for j in status["jobs"] if j["kind"] == interpret.RUN_MERGE][0]["status"] == interpret.MERGE_WAITING

    # Second review done: the worker's after-terminal hook opens the barrier and queues exactly one merge job.
    assert process_one()
    with platform_session() as platform:
        merges = merge_jobs(platform, request_id)
        assert len(merges) == 1 and merges[0].status == "queued"
        merge_job = merges[0]
        merge_payload = merge_job.payload
        assert merge_payload["run_id"] == merge_run_id
        assert merge_payload["request_id"] == str(request_id) and merge_payload["sector"] == "industrial_goods"
        assert set(merge_payload["successful_company_ids"]) == {a, b} and merge_payload["failed_company_ids"] == []
        assert str(review_run.id) in merge_payload["successful_run_ids"]
        # Releasing again is a no-op (stable key).
        ctx = _ctx(platform, headers)
        assert interpret.release_sector_barrier(platform, ctx, str(request_id), "industrial_goods").id == merge_job.id
        assert len(merge_jobs(platform, request_id)) == 1
    with tenant_session(company_schema(a)) as ts:
        handoff = ts.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == review_run.id, AgentRunEvent.event_type == "handoff"))
        assert handoff.data["to"] == interpret.RUN_MERGE
        assert handoff.data["job_id"] == str(merge_job.id) and handoff.data["run_id"] == merge_run_id
    status = client.get(f"/api/portfolio/interpretation/{request_id}", headers=headers).json()
    assert status["done"] is False and {j["id"] for j in status["jobs"]} == {*report["review"].values(), str(merge_job.id)}

    # The worker executes the merge (stub model: no live key in tests).
    while process_one():
        pass
    with platform_session() as platform:
        for jid in (*report["review"].values(), str(merge_job.id)):
            job = platform.get(Job, uuid.UUID(jid))
            assert job.status == "succeeded", (job.kind, job.error)
    status = client.get(f"/api/portfolio/interpretation/{request_id}", headers=headers).json()
    assert status["done"] is True and status["failed"] == 0 and status["succeeded"] == len(status["jobs"]) == 3
    assert {j["kind"] for j in status["jobs"]} == {interpret.RUN_REVIEW, interpret.RUN_MERGE}
    with tenant_session(company_schema(a)) as ts:
        run = ts.get(AgentRun, review_run.id)
        assert run.status == "succeeded"
        structured = ts.scalars(select(Finding).where(Finding.run_id == run.id, Finding.effect.is_not(None))).all()
        assert structured, "deterministic reviewer checks carry structured metadata"
        for f in structured:
            assert f.finding_type and f.severity and f.effect in (interpret.EFFECT_BLOCK, interpret.EFFECT_DEGRADE, interpret.EFFECT_ENRICH)
            assert f.blocking == (f.effect == interpret.EFFECT_BLOCK)
            assert f.affected_entities and all(e["type"] and e["id"] for e in f.affected_entities)
        started = ts.scalar(select(AgentRunEvent).where(AgentRunEvent.run_id == run.id, AgentRunEvent.data["message"].astext == "started"))
        assert started is not None
    with tenant_session(home_schema(firm_id)) as ts:
        merge_run = ts.get(AgentRun, uuid.UUID(merge_run_id))
        assert merge_run.status == "succeeded" and merge_run.job_id == merge_job.id
        started = ts.scalar(
            select(AgentRunEvent).where(AgentRunEvent.run_id == merge_run.id, AgentRunEvent.data["message"].astext == "started")
        )
        assert set(started.data["parent_run_ids"]) == set(merge_payload["successful_run_ids"])
        assert started.data["failed_company_ids"] == []
    with platform_session() as platform:
        for opp in platform.scalars(select(Opportunity).where(Opportunity.firm_id == firm_id)):
            assert opp.lineage["merge_run_id"] == merge_run_id and opp.lineage["request_id"] == str(request_id)
            assert set(opp.lineage["from_runs"]) <= set(merge_payload["successful_run_ids"])
            assert opp.lineage["effect"] in (None, interpret.EFFECT_DEGRADE, interpret.EFFECT_ENRICH)
            assert all(e["recordIds"] for e in opp.evidence), "opportunities cite canonical record ids"

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


def test_barrier_waits_for_retries_then_merges_over_successful_companies_only(client, source_store, monkeypatch):  # noqa: F811
    """A reviewer that fails keeps the barrier closed while it can still retry; once it fails permanently
    the barrier opens, the merge runs over the companies whose reviews succeeded, and the failed scope
    is named in the merge trace instead of blocking the sector forever."""
    headers, firm_id = make_firm()
    a = new_company(client, headers, "Northfield Industrial Components", "Industrial distribution")
    b = new_company(client, headers, "Keystone Bearing & Drive", "Industrial distribution")
    for cid, root in ((a, NORTHFIELD), (b, KEYSTONE)):
        import_table(client, headers, cid, root / "03_procurement" / "suppliers.csv", "vendor_master")
        import_table(client, headers, cid, root / "03_procurement" / "purchase_orders.csv", "purchase_orders")
        import_table(client, headers, cid, root / "03_procurement" / "purchase_order_lines.csv", "purchase_order_lines")

    real_review = interpret.review_company

    def flaky_review(platform, ctx, company, *args, **kwargs):
        if str(company.id) == b:
            raise RuntimeError("keystone review exploded")
        return real_review(platform, ctx, company, *args, **kwargs)

    monkeypatch.setattr(interpret, "review_company", flaky_review)
    report = client.post("/api/portfolio/interpretation", headers=headers).json()
    request_id = report["request_id"]
    with platform_session() as platform:
        platform.get(Job, uuid.UUID(report["review"][a])).run_at = datetime.now(UTC) - timedelta(seconds=1)
        platform.commit()
    while process_one():
        pass
    # a succeeded, b failed once and is queued for retry (not terminal): no merge yet.
    with platform_session() as platform:
        ja, jb = (platform.get(Job, uuid.UUID(report["review"][c])) for c in (a, b))
        assert ja.status == "succeeded"
        assert jb.status == "queued" and jb.attempts == 1 and "exploded" in jb.error
        assert merge_jobs(platform, request_id) == []
    status = client.get(f"/api/portfolio/interpretation/{request_id}", headers=headers).json()
    merge_view = next(j for j in status["jobs"] if j["kind"] == interpret.RUN_MERGE)
    assert status["done"] is False and merge_view["status"] == interpret.MERGE_WAITING and merge_view["waiting_on"] == [b]

    # Exhaust b's retries: permanent failure is terminal, so the barrier opens.
    with platform_session() as platform:
        jb = platform.get(Job, uuid.UUID(report["review"][b]))
        jb.max_attempts = jb.attempts + 1
        jb.run_at = datetime.now(UTC) - timedelta(seconds=1)
        platform.commit()
    assert process_one()
    with platform_session() as platform:
        assert platform.get(Job, uuid.UUID(report["review"][b])).status == "failed"
        (merge,) = merge_jobs(platform, request_id)
        assert merge.status == "queued"
        assert merge.payload["successful_company_ids"] == [a] and merge.payload["failed_company_ids"] == [b]
        assert merge.payload["successful_run_ids"] == [platform.get(Job, uuid.UUID(report["review"][a])).payload["run_id"]]
        merge_run_id, merge_id = merge.payload["run_id"], merge.id
    with tenant_session(company_schema(b)) as ts:
        assert ts.scalar(select(AgentRunEvent).where(AgentRunEvent.event_type == "handoff")) is None, "failed reviewers hand nothing off"

    while process_one():
        pass
    with platform_session() as platform:
        assert platform.get(Job, merge_id).status == "succeeded", platform.get(Job, merge_id).error
    status = client.get(f"/api/portfolio/interpretation/{request_id}", headers=headers).json()
    assert status["done"] is True and status["failed"] == 1 and status["succeeded"] == 2
    with tenant_session(home_schema(firm_id)) as ts:
        events = ts.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == uuid.UUID(merge_run_id)).order_by(AgentRunEvent.seq)).all()
        started = next(e for e in events if e.data.get("message") == "started")
        assert started.data["failed_company_ids"] == [b] and started.data["companies"] == ["Northfield"]
        excluded = [e for e in events if e.event_type == "error" and e.data.get("excluded")]
        assert len(excluded) == 1 and excluded[0].data["company_id"] == b
        assert "Keystone" in excluded[0].data["message"] and "excluded from this analysis" in excluded[0].data["message"]
        result = next(e for e in events if e.event_type == "result")
        assert result.data["excluded_company_ids"] == [b]
    # A single-company sector produces no cross-company opportunity, and none cites the excluded company.
    snap = client.get("/api/portfolio", headers=headers).json()
    assert all(b not in o["companyIds"] for o in snap["opportunities"])


def _rf(fid, finding_type, effect, company="Northfield", run="run-1"):
    return interpret.ReviewerFinding(fid, run, company, "cid", f"title {fid}", "detail", finding_type, "Medium", effect)


def test_validate_candidate_intersects_record_ids_and_ranks_effects():
    """Finding-aware validation is a set intersection, not prose: only findings about the candidate's own
    canonical records count, the strongest effect wins (block > degrade > enrich), and the same finding
    can mean different things to different candidate kinds."""
    dup = _rf("f-dup", "data_quality.duplicate_entity", interpret.EFFECT_DEGRADE)
    bad_map = _rf("f-map", "data_quality.mapping_suspect", interpret.EFFECT_BLOCK, company="Keystone")
    stale = _rf("f-stale", "data_quality.stale_count", interpret.EFFECT_ENRICH)
    index = {"Northfield": {"v1": [dup], "i9": [stale]}, "Keystone": {"l7": [bad_map]}}

    # No intersection -> no effect.
    assert interpret.validate_candidate("purchasing_price_gap", {"record_ids": {"Northfield": ["v2"]}}, index) == (None, [])
    # Duplicate vendors degrade a price-gap estimate...
    effect, hits = interpret.validate_candidate("purchasing_price_gap", {"record_ids": {"Northfield": ["v1"]}}, index)
    assert effect == interpret.EFFECT_DEGRADE and [f.id for f, _ in hits] == ["f-dup"]
    # ...but enrich a vendor-consolidation thesis (same finding, different candidate kind).
    effect, hits = interpret.validate_candidate("vendor_consolidation", {"record_ids": {"Northfield": ["v1"]}}, index)
    assert effect == interpret.EFFECT_ENRICH and hits[0][1] == interpret.EFFECT_ENRICH
    # A suspect money mapping on any company behind the candidate blocks it; strongest effect is reported first.
    effect, hits = interpret.validate_candidate(
        "purchasing_price_gap", {"record_ids": {"Northfield": ["v1", "i9"], "Keystone": ["l7"]}}, index
    )
    assert effect == interpret.EFFECT_BLOCK
    assert [(f.id, e) for f, e in hits] == [("f-map", "block"), ("f-dup", "degrade"), ("f-stale", "enrich")]

    # Effects are applied in code: degrade lowers confidence and records the assumption; enrich only adds context.
    row = {"confidence": 0.8, "assumptions": ["existing"]}
    degraded = interpret._apply_effect(row, interpret.EFFECT_DEGRADE, [(dup, interpret.EFFECT_DEGRADE), (stale, interpret.EFFECT_ENRICH)])
    assert degraded["confidence"] == 0.6 and degraded["assumptions"][0] == "existing" and len(degraded["assumptions"]) == 3
    assert interpret._apply_effect(row, interpret.EFFECT_ENRICH, [(stale, interpret.EFFECT_ENRICH)])["confidence"] == 0.8
    assert interpret._apply_effect(row, None, []) is row
