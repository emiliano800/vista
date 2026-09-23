"""Assembles the analyst workspace snapshot for a firm: canonical records per
company, backend-computed metrics, integration progress, the attention queue
and the agent-interpretation layer (opportunities, findings, agents, runs)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vista.config import settings
from vista.db import tenant_session
from vista.models.platform import Opportunity, PortfolioActivity, Tenant
from vista.models.tenant import (
    Customer,
    ImportException,
    ImportJob,
    InventoryBalance,
    Invoice,
    Policy,
    PurchaseOrder,
    PurchaseOrderLine,
    RecordProvenance,
    SourceFile,
    Subscription,
    Task,
    Vendor,
    VendorPurchase,
)
from vista.portfolio import ledger
from vista.portfolio import serializers as ser
from vista.portfolio.access import CompanyRef, FirmContext, company_session, reporting_period, today
from vista.portfolio.metrics import CompanyMetrics, company_metrics, portfolio_metrics

INTEGRATION_STEPS = [
    ("profile", "Company profile"),
    ("customers", "Customers imported"),
    ("invoices", "Invoices / AR imported"),
    ("vendors", "Vendors & purchases imported"),
    ("software", "Software inventory"),
    ("operations", "Policies / purchasing & inventory imported"),
    ("exceptions", "Import exceptions resolved"),
    ("analysis", "Portfolio analysis run"),
]
CLOSED_TASK = ("Complete", "Dismissed")
CLOSED_OPP = ("Dismissed", "Realized")


def _prov_index(session: Session) -> dict[tuple[str, uuid.UUID], RecordProvenance]:
    rows = session.scalars(select(RecordProvenance).where(RecordProvenance.field_name.is_(None))).all()
    return {(p.entity_type, p.entity_id): p for p in rows}


def company_records(session: Session, include_provenance: bool = True) -> dict:
    prov = _prov_index(session) if include_provenance else {}
    vendors = session.scalars(select(Vendor).order_by(Vendor.normalized_name)).all()
    vendor_name = {v.id: v.normalized_name for v in vendors}
    line_count = dict(
        session.execute(
            select(PurchaseOrderLine.purchase_order_id, func.count())
            .where(PurchaseOrderLine.purchase_order_id.is_not(None))
            .group_by(PurchaseOrderLine.purchase_order_id)
        ).all()
    )
    return {
        "customers": [
            ser.customer(c, prov.get(("customer", c.id)))
            for c in session.scalars(select(Customer).order_by(Customer.created_at, Customer.display_name))
        ],
        "invoices": [
            ser.invoice(i, prov.get(("invoice", i.id)))
            for i in session.scalars(select(Invoice).order_by(Invoice.issue_date, Invoice.source_invoice_number))
        ],
        "vendors": [ser.vendor(v, prov.get(("vendor", v.id))) for v in vendors],
        "purchases": [
            ser.purchase(p, vendor_name.get(p.vendor_id, ""), prov.get(("vendor_purchase", p.id)))
            for p in session.scalars(select(VendorPurchase).order_by(VendorPurchase.purchase_date, VendorPurchase.sku))
        ],
        "subscriptions": [
            ser.subscription(s, prov.get(("subscription", s.id)))
            for s in session.scalars(select(Subscription).order_by(Subscription.product_name))
        ],
        "policies": [
            ser.policy(p, prov.get(("policy", p.id)))
            for p in session.scalars(select(Policy).order_by(Policy.expiration_date, Policy.policy_number))
        ],
        "purchaseOrders": [
            ser.purchase_order(po, line_count.get(po.id, 0), prov.get(("purchase_order", po.id)))
            for po in session.scalars(select(PurchaseOrder).order_by(PurchaseOrder.po_date, PurchaseOrder.po_number))
        ],
        "purchaseOrderLines": [
            ser.purchase_order_line(line, prov.get(("purchase_order_line", line.id)))
            for line in session.scalars(select(PurchaseOrderLine).order_by(PurchaseOrderLine.po_number, PurchaseOrderLine.line_number))
        ],
        "inventory": [
            ser.inventory_balance(b, prov.get(("inventory_balance", b.id)))
            for b in session.scalars(select(InventoryBalance).order_by(InventoryBalance.warehouse, InventoryBalance.item_id))
        ],
    }


def company_imports(session: Session) -> tuple[list[dict], list[dict]]:
    jobs = session.execute(
        select(ImportJob, SourceFile).join(SourceFile, SourceFile.id == ImportJob.source_file_id).order_by(ImportJob.created_at)
    ).all()
    exceptions = session.scalars(select(ImportException).order_by(ImportException.created_at)).all()
    by_job: dict[uuid.UUID, list[ImportException]] = {}
    for x in exceptions:
        by_job.setdefault(x.import_job_id, []).append(x)
    out_jobs = []
    for job, file in jobs:
        j = ser.import_job(job, file)
        xs = by_job.get(job.id, [])
        j["files"] = [f.strip() for f in file.filename.split(",")]
        j["accepted"] = job.records_imported
        j["reviewed"] = job.records_needing_review + sum(1 for x in xs if x.status == "resolved" and x.resolution != "Skip")
        j["rejected"] = job.records_rejected
        j["exceptions"] = [ser.import_exception(x) for x in xs]
        out_jobs.append(j)
    open_exceptions = [ser.import_exception(x) for x in exceptions if x.status == "open"]
    return out_jobs, open_exceptions


def integration(c: dict, metrics: CompanyMetrics, open_exceptions: int, jobs: int) -> dict:
    status = {
        "profile": "Complete" if c["name"] and c["location"] and c["acquired"] else "Not started",
        "customers": "Complete" if metrics.customers else "Not started",
        "invoices": "Complete" if metrics.invoiceCount or metrics.outstandingInvoiceCount else "Not started",
        "vendors": "Complete" if metrics.vendorCount else "Not started",
        "software": "Complete" if metrics.subscriptionCount else "Not started",
        "operations": "Complete" if metrics.policyCount or metrics.purchaseOrderCount or metrics.inventoryItems else "Not started",
        "exceptions": "Needs review" if open_exceptions else ("Complete" if jobs else "Not started"),
        "analysis": "Complete" if c["analysisRunAt"] else ("In progress" if metrics.customers else "Not started"),
    }
    steps = [{"key": k, "label": label, "status": status[k]} for k, label in INTEGRATION_STEPS]
    complete = sum(1 for s in steps if s["status"] == "Complete")
    label = "Complete" if complete == len(steps) else ("Not started" if complete == 0 else "In progress")
    return {"steps": steps, "complete": complete, "total": len(steps), "label": label}


def _money(n: float) -> str:
    return f"${n / 1e6:.1f}M" if n >= 1e6 else f"${round(n / 1e3)}K"


def summary(c: dict, m: CompanyMetrics, integ: dict, open_x: int, opps: list[dict]) -> str:
    cross = sum(1 for o in opps if len(o["companyIds"]) > 1)
    parts = [
        f"{c['name']} is a {c['industry'].lower()} business in {c['location']} with {m.customers:,} imported customers "
        f"and {_money(m.outstandingAr)} of outstanding AR ({_money(m.overdueAr)} past due).",
        f"Vendor spend in the period is {_money(m.vendorSpend)} across {m.vendorCount} vendors; "
        f"software runs {_money(m.softwareAnnual)} a year on {m.subscriptionCount} subscriptions.",
        f"{integ['complete']} of {integ['total']} integration steps are complete"
        + (f" and {open_x} import exception{'' if open_x == 1 else 's'} still need{'s' if open_x == 1 else ''} review" if open_x else "")
        + ".",
    ]
    if opps:
        parts.append(
            f"Vista has {len(opps)} open opportunit{'y' if len(opps) == 1 else 'ies'} involving {c['name']}"
            f"{f', {cross} of them cross-company' if cross else ''}."
        )
    elif c["analysisRunAt"]:
        parts.append("Portfolio analysis found no open opportunities involving this company.")
    else:
        parts.append("Portfolio analysis has not run for this company yet.")
    return " ".join(parts)


def load_company(ref: CompanyRef, include_records: bool = True) -> tuple[dict, CompanyMetrics]:
    period = reporting_period()
    now = today()
    c = ser.company_profile(ref.row)
    with company_session(ref) as session:
        m = company_metrics(session, period, now)
        jobs, open_x = company_imports(session)
        records = company_records(session) if include_records else {}
        tasks = [ser.task(t) for t in session.scalars(select(Task).order_by(Task.created_at))]
        # The agent layer is read straight from the ledger the company workspace and the
        # recorder write to; nothing is mirrored for the analyst.
        agents, runs, findings = ledger.tenant_ledger(session, ref.slug, ref.id)
    c.update(records)
    c["importJobs"] = jobs
    c["importExceptions"] = open_x
    c["metrics"] = m.as_dict()
    c["integration"] = integration(c, m, len(open_x), len(jobs))
    c["_tasks"] = tasks
    c["_agents"] = agents
    c["_runs"] = runs
    c["_findings"] = findings
    return c, m


def firm_ledger(session: Session, ctx: FirmContext) -> tuple[list[dict], list[dict], list[dict]]:
    """Runs that belong to the firm rather than one company (the Sector Merger's, in the
    firm's home tenant). They carry no companyId; their findings cite the companies."""
    home = session.get(Tenant, ctx.firm.home_tenant_id)
    if home is None:
        return [], [], []
    with tenant_session(home.schema_name) as ts:
        return ledger.tenant_ledger(ts, ctx.firm.slug, None)


def _days_between(a: str | None, now: date) -> int:
    if not a:
        return 0
    d = date.fromisoformat(a[:10])
    return (now - d).days


def attention_queue(companies: list[dict], opportunities: list[dict], agents: list[dict], findings: list[dict], now: date) -> list[dict]:
    items: list[dict] = []

    def push(item: dict) -> None:
        age = _days_between(item.get("since"), now)
        items.append({"age": "today" if age <= 0 else f"{age}d", **item})

    for c in companies:
        cid = c["id"]
        m = c["metrics"]
        open_x = len(c["importExceptions"])
        if open_x:
            since = c["importJobs"][-1]["createdAt"] if c["importJobs"] else c["acquired"]
            push(
                {
                    "companyId": cid,
                    "type": "Import review",
                    "text": f"{open_x} record{'' if open_x == 1 else 's'} need mapping review",
                    "since": since,
                    "severity": "High" if open_x > 10 else "Medium",
                    "cta": "Review records",
                    "href": f"/acquisitions/?id={cid}",
                }
            )
        n90 = m["overdue90Count"]
        if n90:
            push(
                {
                    "companyId": cid,
                    "type": "Working capital",
                    "text": f"{n90} invoice{'' if n90 == 1 else 's'} >90 days overdue",
                    "since": m["oldestOverdue90DueDate"],
                    "severity": "High" if n90 >= 5 else "Medium",
                    "cta": "Inspect evidence",
                    "href": f"/company/?id={cid}&tab=finance&filter=overdue90",
                }
            )
        for s in c.get("subscriptions", []):
            if not s["renewalDate"]:
                continue
            days = -_days_between(s["renewalDate"], now)
            if 0 <= days <= 30:
                push(
                    {
                        "companyId": cid,
                        "type": "Renewal",
                        "text": f"{s['product']} renews in {days} days",
                        "since": now.isoformat(),
                        "severity": "High" if days <= 14 else "Medium",
                        "cta": "Assign task",
                        "href": f"/company/?id={cid}&tab=software",
                    }
                )
        for a in agents:
            if a["companyId"] == cid and a["review"]:
                push(
                    {
                        "companyId": cid,
                        "type": "Agent exception",
                        "text": f"{a['name']} run requires human review",
                        "since": a["lastRunAt"],
                        "severity": "Medium",
                        "cta": "Inspect evidence",
                        "href": f"/agents/?company={cid}",
                    }
                )
        for f in findings:
            if f["companyId"] == cid and f["status"] == "Open" and f["severity"] == "High":
                push(
                    {
                        "companyId": cid,
                        "type": "Finding",
                        "text": f["title"],
                        "since": f["foundAt"],
                        "severity": "High",
                        "cta": "Inspect evidence",
                        "href": f"/company/?id={cid}&tab=findings",
                    }
                )
        for o in opportunities:
            if o["status"] == "New" and o["companyIds"] and o["companyIds"][0] == cid:
                push(
                    {
                        "companyId": cid,
                        "type": "Opportunity",
                        "text": f"{o['id']}: {o['title']}",
                        "since": o["foundAt"],
                        "severity": "Low",
                        "cta": "Review opportunity",
                        "href": f"/opportunities/?id={o['id']}",
                    }
                )
    rank = {"High": 0, "Medium": 1, "Low": 2}
    items.sort(key=lambda i: (rank[i["severity"]], i.get("since") or ""))
    return items


def firm_layer(session: Session, ctx: FirmContext, activity_limit: int = 60) -> tuple[list[dict], list[dict]]:
    opps = [
        ser.opportunity(o)
        for o in session.scalars(select(Opportunity).where(Opportunity.firm_id == ctx.firm.id).order_by(Opportunity.found_at))
    ]
    acts = [
        ser.activity(a)
        for a in session.scalars(
            select(PortfolioActivity)
            .where(PortfolioActivity.firm_id == ctx.firm.id)
            .order_by(PortfolioActivity.at.desc())
            .limit(activity_limit)
        )
    ]
    return opps, acts


def snapshot(session: Session, ctx: FirmContext, include_records: bool = True) -> dict:
    """Everything the analyst UI needs, scoped to the firm's companies."""
    now = today()
    period = reporting_period(now)
    loaded = [load_company(ref, include_records) for ref in ctx.companies]
    companies = []
    tasks: list[dict] = []
    agents: list[dict] = []
    runs: list[dict] = []
    findings: list[dict] = []
    for c, _m in loaded:
        tasks += c.pop("_tasks")
        agents += c.pop("_agents")
        runs += c.pop("_runs")
        findings += c.pop("_findings")
        companies.append(c)
    firm_agents, firm_runs, firm_findings = firm_ledger(session, ctx)
    agents += firm_agents
    runs += firm_runs
    findings += firm_findings
    opportunities, activity = firm_layer(session, ctx)
    open_opps = [o for o in opportunities if o["status"] not in CLOSED_OPP]
    for c, m in loaded:
        mine = [o for o in open_opps if c["id"] in o["companyIds"]]
        c["metrics"]["openTasks"] = sum(1 for t in tasks if t["companyId"] == c["id"] and t["status"] not in CLOSED_TASK)
        c["metrics"]["openOpportunities"] = len(mine)
        c["metrics"]["automationCandidates"] = sum(1 for o in mine if o["category"] == "Process automation") + sum(
            1
            for f in findings
            if f["companyId"] == c["id"]
            and any(w in f"{f['title']} {f['detail']}".lower() for w in ("re-keys", "copies", "manual", "repetitive"))
        )
        c["summary"] = summary(c, m, c["integration"], len(c["importExceptions"]), mine)
    metrics = portfolio_metrics([m for _c, m in loaded])
    metrics["openOpportunities"] = len(open_opps)
    metrics["openTasks"] = sum(1 for t in tasks if t["status"] not in CLOSED_TASK)
    return {
        "version": 2,
        "generatedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
        "today": now.isoformat(),
        "period": {"label": period.label, "start": period.start.isoformat(), "end": period.end.isoformat()},
        "syntheticData": settings.use_synthetic_data,
        "firm": {"id": str(ctx.firm.id), "name": ctx.firm.name, "slug": ctx.firm.slug},
        "analyst": {
            "name": ctx.actor,
            "email": ctx.principal.email,
            "role": ctx.membership.role if ctx.membership else "company",
            "firm": ctx.firm.name,
        },
        "companies": companies,
        "metrics": metrics,
        "attention": attention_queue(companies, opportunities, agents, findings, now),
        "tasks": tasks,
        "opportunities": opportunities,
        "findings": findings,
        "agents": agents,
        "runs": runs,
        "activity": activity,
    }
