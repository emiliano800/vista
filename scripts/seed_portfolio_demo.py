"""Seed the PE analyst demo portfolio (Northstar HVAC Holdings → Harbor, Summit,
Cedar) into the canonical Postgres tables the import pipeline also writes to.

Every row is tagged synthetic_demo=true / data_source_type="synthetic_seed" and
keyed by a deterministic UUID5 of its fixture id, so re-running the script
updates rows in place instead of duplicating them.

Usage (against the DB in .env, after `docker compose up -d postgres minio`):
    uv run python scripts/seed_portfolio_demo.py
    uv run python scripts/seed_portfolio_demo.py --skip-cedar   # leave Cedar for the live import demo
    uv run python scripts/seed_portfolio_demo.py --analyst-key <64 hex>   # stamps a new key; omit to keep the existing one
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vista.config import settings  # noqa: E402
from vista.db import platform_session, tenant_session  # noqa: E402
from vista.models.platform import Firm, FirmCompany, FirmMembership, Opportunity, PortfolioActivity, Tenant, User  # noqa: E402
from vista.models.tenant import (  # noqa: E402
    Customer,
    ImportJob,
    Invoice,
    RecordProvenance,
    SourceFile,
    Subscription,
    Task,
    Vendor,
    VendorPurchase,
    WorkspaceAgent,
    WorkspaceAgentRun,
    WorkspaceFinding,
)
from vista.portfolio.service import ensure_company_deal  # noqa: E402
from vista.security import token_digest  # noqa: E402
from vista.storage import ensure_bucket  # noqa: E402
from vista.tenancy import migrate_platform, migrate_tenant_schema  # noqa: E402

FIXTURE = ROOT / "synthetic_data" / "portfolio_demo" / "portfolio.json"
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://vista.example/portfolio_demo")
FIRM = {"name": "Northstar HVAC Holdings", "slug": "northstar"}
ANALYST = {"name": "Sarah Okafor", "email": "sarah@northstarhvac.com", "role": "analyst"}
SEED = {"data_source_type": "synthetic_seed", "synthetic_demo": True}


def sid(key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, key)


def schema_for(slug: str) -> str:
    return f"t_{sid(f'schema:{slug}').hex[:12]}"


def ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def dec(v, q: str = "0.01") -> Decimal:
    return Decimal(str(v if v is not None else 0)).quantize(Decimal(q))


def upsert(session: Session, model, id_: uuid.UUID, **values):
    """Insert-or-update by primary key; returns the persistent row."""
    row = session.get(model, id_)
    if row is None:
        row = model(id=id_, **values)
        session.add(row)
    else:
        columns = model.__table__.columns.keys()
        for k, v in values.items():
            if k not in columns:
                raise AttributeError(f"{model.__name__} has no column {k!r}")
            setattr(row, k, v)  # noqa: B010 - generic column upsert
    return row


# ---- Platform: firm, analyst, companies ----------------------------------------------------


def seed_firm(session: Session, analyst_key: str | None) -> Firm:
    home = upsert(session, Tenant, sid("tenant:firm"), name=FIRM["name"], schema_name=schema_for("firm"))
    session.flush()
    # Reuse the analyst account that already exists rather than minting a second
    # one: firm membership hangs off user_id, and api_token_hash is unique, so a
    # duplicate would either collide with the live key or leave it without a
    # firm. Only stamp a new key when one is given explicitly.
    user = None
    if analyst_key:
        user = session.scalar(select(User).where(User.api_token_hash == token_digest(analyst_key)))
    if user is None:
        user = session.scalar(select(User).where(User.email == ANALYST["email"]).order_by(User.created_at))
    if user is None:
        if not analyst_key:
            raise SystemExit(f"No user {ANALYST['email']!r} exists yet; pass --analyst-key to create one.")
        user = User(id=sid("user:analyst"), tenant_id=home.id, email=ANALYST["email"], role="member")
        session.add(user)
    if analyst_key:
        user.api_token_hash = token_digest(analyst_key)
    session.flush()
    firm = upsert(session, Firm, sid("firm"), name=FIRM["name"], slug=FIRM["slug"], home_tenant_id=home.id, synthetic_demo=True)
    session.flush()
    upsert(
        session,
        FirmMembership,
        sid("membership:analyst"),
        firm_id=firm.id,
        user_id=user.id,
        role=ANALYST["role"],
        display_name=ANALYST["name"],
    )
    return firm


def seed_company(session: Session, firm: Firm, c: dict) -> FirmCompany:
    tenant = upsert(session, Tenant, sid(f"tenant:{c['id']}"), name=c["name"], schema_name=schema_for(c["id"]))
    session.flush()
    fc = upsert(
        session,
        FirmCompany,
        sid(f"company:{c['id']}"),
        firm_id=firm.id,
        tenant_id=tenant.id,
        slug=c["id"],
        name=c["name"],
        location=c.get("location", ""),
        industry=c.get("industry", ""),
        description=c.get("description", ""),
        acquisition_date=d(c.get("acquired")),
        status="active",
        synthetic_demo=True,
        analysis_run_at=ts(c.get("analysisRunAt")),
    )
    return fc


# ---- Tenant: canonical records + provenance --------------------------------------------------


def seed_import_job(session: Session, company_id: uuid.UUID, job: dict) -> ImportJob:
    """One completed synthetic import job per company so provenance has something to point at."""
    file = upsert(
        session,
        SourceFile,
        sid(f"file:{job['id']}"),
        company_id=company_id,
        filename=", ".join(job["files"]),
        storage_key="",
        mime_type="application/octet-stream",
        file_size=0,
        content_hash=sid(f"hash:{job['id']}").hex,
        uploaded_at=ts(job["createdAt"]),
        status="processed",
    )
    session.flush()
    return upsert(
        session,
        ImportJob,
        sid(f"job:{job['id']}"),
        company_id=company_id,
        source_file_id=file.id,
        status="completed",
        dataset_type="bundle",
        detection_confidence=1.0,
        sheet_name="",
        columns=[],
        processor="synthetic_seed",
        created_at=ts(job["createdAt"]),
        records_detected=job["accepted"] + job["reviewed"] + job["rejected"],
        records_imported=job["accepted"],
        records_needing_review=job["reviewed"],
        records_rejected=job["rejected"],
        started_at=ts(job["createdAt"]),
        completed_at=ts(job["createdAt"]),
    )


def seed_provenance(session: Session, entity_type: str, entity_id: uuid.UUID, prov: dict | None, job: ImportJob) -> None:
    if not prov:
        return
    upsert(
        session,
        RecordProvenance,
        sid(f"prov:{entity_type}:{entity_id}"),
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=None,
        source_file_id=job.source_file_id,
        source_filename=prov.get("file", ""),
        sheet_name=prov.get("sheet", ""),
        row_number=prov.get("row"),
        raw_value=prov.get("original", {}),
        normalized_value=prov.get("normalized", {}),
        import_job_id=job.id,
        confidence=float(prov.get("confidence", 1)),
        human_verified=prov.get("review") == "human-reviewed",
    )


def seed_records(session: Session, fc: FirmCompany, c: dict) -> dict[str, int]:
    job = seed_import_job(session, fc.id, c["importJobs"][0])
    base = {"company_id": fc.id, "source_file_id": job.source_file_id, "import_job_id": job.id, **SEED}
    counts: dict[str, int] = {}

    for x in c["customers"]:
        row = upsert(
            session,
            Customer,
            sid(x["id"]),
            **base,
            legal_name=x["name"],
            display_name=x["name"],
            source_customer_id=x["id"],
            contact_name=x.get("contact", ""),
            email=x.get("email", ""),
            phone=x.get("phone", ""),
            address_line_1=x.get("address", ""),
            city=x.get("city", ""),
            state=x.get("state", ""),
            postal_code=x.get("zip", ""),
            service_type=x.get("serviceType", ""),
            status=x.get("status", "active"),
        )
        seed_provenance(session, "customer", row.id, x.get("provenance"), job)
    counts["customers"] = len(c["customers"])

    for x in c["invoices"]:
        row = upsert(
            session,
            Invoice,
            sid(x["id"]),
            **base,
            customer_id=sid(x["customerId"]) if x.get("customerId") else None,
            customer_name=x.get("customerName", ""),
            source_invoice_number=x.get("number", ""),
            issue_date=d(x.get("issueDate")),
            due_date=d(x.get("dueDate")),
            amount=dec(x.get("amount")),
            outstanding_balance=dec(x.get("outstanding")),
            status=x.get("status", "open"),
        )
        seed_provenance(session, "invoice", row.id, x.get("provenance"), job)
    counts["invoices"] = len(c["invoices"])

    for x in c["vendors"]:
        row = upsert(
            session,
            Vendor,
            sid(x["id"]),
            **base,
            normalized_name=x["name"],
            source_name=x.get("sourceName", x["name"]),
            contact_name="",
            email=x.get("contact", ""),
            phone="",
            status="active",
        )
        seed_provenance(session, "vendor", row.id, x.get("provenance"), job)
    counts["vendors"] = len(c["vendors"])

    for x in c["purchases"]:
        row = upsert(
            session,
            VendorPurchase,
            sid(x["id"]),
            **base,
            vendor_id=sid(x["vendorId"]) if x.get("vendorId") else None,
            purchase_date=d(x.get("date")),
            sku=x.get("sku", ""),
            item_description=x.get("description", ""),
            quantity=dec(x.get("quantity"), "0.001"),
            unit=x.get("unit", ""),
            unit_price=dec(x.get("unitPrice"), "0.0001"),
            total_amount=dec(x.get("total")),
        )
        seed_provenance(session, "vendor_purchase", row.id, x.get("provenance"), job)
    counts["purchases"] = len(c["purchases"])

    for x in c["subscriptions"]:
        monthly = dec(x.get("monthlyCost"))
        row = upsert(
            session,
            Subscription,
            sid(x["id"]),
            **base,
            vendor_name=x.get("vendor", x["product"]),
            product_name=x["product"],
            category=x.get("category", ""),
            monthly_cost=monthly,
            annual_cost=dec(monthly * 12),
            seat_count=int(x.get("seats") or 0),
            renewal_date=d(x.get("renewalDate")),
            contract_end_date=d(x.get("contractEnd")),
            restrictions_notes=x.get("notes", ""),
        )
        seed_provenance(session, "subscription", row.id, x.get("provenance"), job)
    counts["subscriptions"] = len(c["subscriptions"])
    return counts


def seed_workspace(session: Session, fc: FirmCompany, fixture: dict, slug: str) -> dict[str, int]:
    """Tasks, agents, runs and findings that belong to this company (agent layer, kept apart from facts)."""
    counts = {"tasks": 0, "agents": 0, "runs": 0, "findings": 0}
    for t in fixture["tasks"]:
        if t["companyId"] != slug:
            continue
        upsert(
            session,
            Task,
            sid(f"task:{t['id']}"),
            company_id=fc.id,
            ref=t["id"],
            title=t["title"],
            description=t.get("description", ""),
            category=t.get("category", "Integration"),
            source_type=t.get("sourceType"),
            source_id=t.get("sourceId"),
            assignee=t.get("assignee") or "",
            priority=t.get("priority", "Medium"),
            status=t.get("status", "Open"),
            due_date=d(t.get("dueDate")),
            outcome=t.get("outcome"),
            outcome_notes=t.get("outcomeNotes") or "",
            realized_value=dec(t["realizedResult"]) if t.get("realizedResult") is not None else None,
            created_by=t.get("createdBy", ""),
            synthetic_demo=True,
            created_at=ts(t.get("createdAt")),
            completed_at=ts(t.get("completedAt")),
        )
        counts["tasks"] += 1
    for a in fixture["agents"]:
        if a["companyId"] != slug:
            continue
        upsert(
            session,
            WorkspaceAgent,
            sid(f"agent:{a['id']}"),
            company_id=fc.id,
            ref=a["id"],
            name=a["name"],
            status=a.get("status", "Active"),
            last_run_at=ts(a.get("lastRunAt")),
            payload={k: a[k] for k in ("represents", "cases", "review", "findings", "lastFailure", "cost") if k in a},
            synthetic_demo=True,
        )
        counts["agents"] += 1
    session.flush()
    for r in fixture["runs"]:
        if r["companyId"] != slug:
            continue
        upsert(
            session,
            WorkspaceAgentRun,
            sid(f"run:{r['id']}"),
            company_id=fc.id,
            agent_id=sid(f"agent:{r['agentId']}"),
            ref=r["id"],
            status=r.get("status", "Complete"),
            started_at=ts(r["startedAt"]),
            needs_review=int(r.get("needsReview") or 0),
            model_cost=dec(r.get("modelCost")),
            payload={k: r[k] for k in ("goal", "sources", "events", "output", "evidence", "corrections") if k in r},
            synthetic_demo=True,
        )
        counts["runs"] += 1
    for f in fixture["findings"]:
        if f["companyId"] != slug:
            continue
        upsert(
            session,
            WorkspaceFinding,
            sid(f"finding:{f['id']}"),
            company_id=fc.id,
            ref=f["id"],
            agent_id=sid(f"agent:{f['agentId']}") if f.get("agentId") else None,
            run_id=sid(f"run:{f['runId']}") if f.get("runId") else None,
            title=f["title"],
            detail=f.get("detail", ""),
            severity=f.get("severity", "Medium"),
            status=f.get("status", "New"),
            found_at=ts(f["foundAt"]),
            synthetic_demo=True,
        )
        counts["findings"] += 1
    return counts


# ---- Platform: opportunities + activity ------------------------------------------------------


def evidence_id(e: dict) -> str:
    """Canonical rows are re-keyed by UUID5; findings keep their display ref (F-201)."""
    return e["id"] if e["entity"] == "finding" else str(sid(e["id"]))


def seed_firm_layer(session: Session, firm: Firm, fixture: dict, slugs: set[str]) -> dict[str, int]:
    company_uuid = {s: str(sid(f"company:{s}")) for s in slugs}
    n_opps = 0
    for o in fixture["opportunities"]:
        if not set(o["companyIds"]) <= slugs:
            continue
        evidence = [
            {**e, "companyId": company_uuid[e["companyId"]], **({"id": evidence_id(e)} if "id" in e else {})} for e in o.get("evidence", [])
        ]
        upsert(
            session,
            Opportunity,
            sid(f"opp:{o['id']}"),
            firm_id=firm.id,
            ref=o["id"],
            title=o["title"],
            category=o.get("category", ""),
            company_ids=[company_uuid[s] for s in o["companyIds"]],
            sku=o.get("sku"),
            confidence=float(o.get("confidence", 0)),
            status=o.get("status", "New"),
            observed_fact=o.get("fact", ""),
            evidence=evidence,
            calculation=o.get("calculation", []),
            potential_benefit=o.get("benefit", ""),
            assumptions=o.get("assumptions", []),
            recommended_action=o.get("nextAction", ""),
            scenario_value=dec(o["potentialValue"]) if o.get("potentialValue") is not None else None,
            realized_value=dec(o["realizedValue"]) if o.get("realizedValue") is not None else None,
            generated_by="synthetic_seed",
            synthetic_demo=True,
            found_at=ts(o["foundAt"]),
        )
        n_opps += 1
    n_act = 0
    for i, a in enumerate(fixture["activity"]):
        if a.get("companyId") and a["companyId"] not in slugs:
            continue
        upsert(
            session,
            PortfolioActivity,
            sid(f"activity:{i}:{a['at']}"),
            firm_id=firm.id,
            company_id=sid(f"company:{a['companyId']}") if a.get("companyId") else None,
            kind=a.get("kind", "system"),
            text=a["text"],
            at=ts(a["at"]),
        )
        n_act += 1
    counters = dict(firm.counters or {})
    for key, fixture_key in (("task", "task"), ("opportunity", "opportunity"), ("job", "job")):
        counters[key] = max(int(counters.get(key, 1)), int(fixture["nextIds"][fixture_key]))
    firm.counters = counters
    return {"opportunities": n_opps, "activity": n_act}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-cedar", action="store_true", help="seed Harbor and Summit only (import Cedar through the wizard)")
    parser.add_argument(
        "--analyst-key",
        default=settings.demo_analyst_key or None,
        help="64-hex analyst access key; omit to keep the existing analyst account's key",
    )
    parser.add_argument("--fixture", default=str(FIXTURE))
    args = parser.parse_args()
    if args.analyst_key and not (len(args.analyst_key) == 64 and all(ch in "0123456789abcdef" for ch in args.analyst_key.lower())):
        parser.error("--analyst-key must be 64 hex characters")

    fixture = json.loads(Path(args.fixture).read_text())
    companies = [c for c in fixture["companies"] if not (args.skip_cedar and c["id"] == "cedar")]
    slugs = {c["id"] for c in companies}

    migrate_platform()
    ensure_bucket()
    with platform_session() as session:
        firm = seed_firm(session, args.analyst_key.lower() if args.analyst_key else None)
        analyst_user_id = session.scalar(select(FirmMembership.user_id).where(FirmMembership.firm_id == firm.id))
        fcs = {c["id"]: seed_company(session, firm, c) for c in companies}
        session.flush()
        schemas = {slug: session.get(Tenant, fc.tenant_id).schema_name for slug, fc in fcs.items()}
        company_ids = {slug: fc.id for slug, fc in fcs.items()}
        firm_counts = seed_firm_layer(session, firm, fixture, slugs)
        session.commit()

    migrate_tenant_schema(schema_for("firm"))
    report = {"firm": FIRM["name"], "analyst": ANALYST["email"], **firm_counts, "companies": {}}
    for c in companies:
        schema = schemas[c["id"]]
        migrate_tenant_schema(schema)
        with platform_session() as platform:
            ensure_company_deal(platform, platform.get(FirmCompany, company_ids[c["id"]]), schema, analyst_user_id)
            platform.commit()
        with tenant_session(schema) as ts_:
            fc = FirmCompany(id=company_ids[c["id"]])
            counts = seed_records(ts_, fc, c)
            counts.update(seed_workspace(ts_, fc, fixture, c["id"]))
            ts_.commit()
        report["companies"][c["name"]] = counts

    # Row counts come from the database so a re-run visibly proves idempotency.
    for c in companies:
        with tenant_session(schemas[c["id"]]) as ts_:
            report["companies"][c["name"]]["db_rows"] = {
                "customers": ts_.scalar(select(func.count()).select_from(Customer)),
                "invoices": ts_.scalar(select(func.count()).select_from(Invoice)),
                "purchases": ts_.scalar(select(func.count()).select_from(VendorPurchase)),
            }
    print(json.dumps({"seeded_at": datetime.now(UTC).isoformat(timespec="seconds"), **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
