"""Write-side operations for the portfolio workspace: company onboarding,
tasks, opportunities, finding triage, exception decisions and the
deterministic purchasing analysis. Every write logs portfolio activity."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vista.db import tenant_session
from vista.models.platform import Firm, FirmCompany, Opportunity, PortfolioActivity, Tenant
from vista.models.tenant import (
    Deal,
    Finding,
    ImportException,
    Task,
    Vendor,
    VendorPurchase,
)
from vista.portfolio.access import CompanyRef, FirmContext, company_session, reporting_period
from vista.tenancy import migrate_tenant_schema

TASK_STATUSES = ("Open", "In progress", "Blocked", "Complete", "Dismissed")
OPP_STATUSES = ("New", "Under review", "Task created", "Validated", "Realized", "Dismissed")


def now() -> datetime:
    return datetime.now(UTC)


def log_activity(
    session: Session, firm_id: uuid.UUID, company_id: uuid.UUID | None, text: str, kind: str, at: datetime | None = None
) -> None:
    session.add(PortfolioActivity(firm_id=firm_id, company_id=company_id, kind=kind, text=text, at=at or now()))


def next_ref(session: Session, firm_id: uuid.UUID, kind: str, prefix: str, width: int = 0) -> str:
    """Firm-wide display sequence (T-106, OP-017…) kept in Firm.counters under a row lock.

    `populate_existing` matters: callers usually have this Firm in the session already
    (`_ctx` loads it at the start of the transaction), and a plain locked select would
    return that cached instance with its stale `counters`, handing out a ref another
    transaction committed in the meantime (the OP-063 unique violation of 2026-09-24).
    Re-reading the row under the lock makes the sequence safe across processes."""
    firm = session.execute(select(Firm).where(Firm.id == firm_id).with_for_update().execution_options(populate_existing=True)).scalar_one()
    counters = dict(firm.counters or {})
    n = int(counters.get(kind, 1))
    counters[kind] = n + 1
    firm.counters = counters
    return f"{prefix}-{str(n).zfill(width)}"


def money2(n: float | Decimal) -> str:
    return f"${float(n):,.2f}"


# ---- Companies -----------------------------------------------------------------


def _slug(session: Session, firm_id: uuid.UUID, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-").split("-")[0] or "company"
    taken = set(session.scalars(select(FirmCompany.slug).where(FirmCompany.firm_id == firm_id)))
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug[:64]


def ensure_company_deal(platform: Session, fc: FirmCompany, schema: str, created_by: uuid.UUID) -> uuid.UUID:
    """The one Deal inside the company tenant that every deal-scoped surface (company
    workspace, recorder) uses for this company. Reuses the tenant's Deal of the same
    name, creates it otherwise, and records the id on `firm_companies.deal_id` so
    analyst and employee views resolve the company to the same rows by id."""
    with tenant_session(schema) as ts:
        deal = ts.get(Deal, fc.deal_id) if fc.deal_id else None
        if deal is None:
            deal = ts.scalar(select(Deal).where(Deal.name == fc.name).order_by(Deal.created_at))
        if deal is None:
            deal = Deal(name=fc.name, created_by=created_by)
            ts.add(deal)
            ts.flush()
        deal_id = deal.id
        ts.commit()
    if fc.deal_id != deal_id:
        fc.deal_id = deal_id
        platform.flush()
    return deal_id


def link_workspace(platform: Session, fc: FirmCompany, tenant: Tenant, deal_id: uuid.UUID | None = None) -> uuid.UUID:
    """Point a firm company at an existing company workspace tenant (one provisioned with
    `create-workspace` before the company was added to the portfolio), so the employees'
    workspace and the analyst's company are the same tenant. The tenant must not already
    belong to another company. Returns the Deal id both views now use; canonical rows in a
    previously linked tenant are not moved — reload them with `load-synthetic`."""
    other = platform.scalar(select(FirmCompany).where(FirmCompany.tenant_id == tenant.id, FirmCompany.id != fc.id))
    if other is not None:
        raise HTTPException(409, f"Tenant {tenant.id} is already linked to company {other.slug}")
    migrate_tenant_schema(tenant.schema_name)
    if deal_id is not None:
        with tenant_session(tenant.schema_name) as ts:
            if ts.get(Deal, deal_id) is None:
                raise HTTPException(404, f"Deal {deal_id} does not exist in tenant {tenant.id}")
    fc.tenant_id, fc.deal_id = tenant.id, deal_id
    platform.flush()
    resolved = ensure_company_deal(platform, fc, tenant.schema_name, _firm_owner(platform, fc.firm_id))
    log_activity(platform, fc.firm_id, fc.id, f"{fc.name} linked to its company workspace (tenant {tenant.id}).", "import")
    platform.commit()
    return resolved


def _firm_owner(platform: Session, firm_id: uuid.UUID) -> uuid.UUID:
    from vista.models.platform import FirmMembership

    user_id = platform.scalar(select(FirmMembership.user_id).where(FirmMembership.firm_id == firm_id).order_by(FirmMembership.created_at))
    if user_id is None:
        raise HTTPException(409, "The firm has no members to own the company's Deal")
    return user_id


def create_company(session: Session, ctx: FirmContext, profile: dict, synthetic_demo: bool | None = None) -> FirmCompany:
    """Creates the company's tenant (own schema, migrated), its Deal, and links both to
    the firm. Status starts as `onboarding` until an import is approved."""
    name = profile["name"].strip()
    if not name:
        raise HTTPException(422, "Company name is required")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    tenant = Tenant(name=name, schema_name=schema)
    session.add(tenant)
    session.flush()
    fc = FirmCompany(
        firm_id=ctx.firm.id,
        tenant_id=tenant.id,
        slug=_slug(session, ctx.firm.id, name),
        name=name,
        location=profile.get("location") or "",
        industry=profile.get("industry") or "",
        description=profile.get("description") or "",
        acquisition_date=date.fromisoformat(profile["acquired"]) if profile.get("acquired") else None,
        status="onboarding",
        synthetic_demo=ctx.firm.synthetic_demo if synthetic_demo is None else synthetic_demo,
    )
    session.add(fc)
    log_activity(session, ctx.firm.id, fc.id, f"{name} added to the portfolio as a new acquisition.", "import")
    session.commit()
    migrate_tenant_schema(schema)
    ensure_company_deal(session, fc, schema, ctx.principal.user_id)
    session.commit()
    return fc


# ---- Tasks -----------------------------------------------------------------------


def create_task(platform: Session, ctx: FirmContext, company: CompanyRef, body: dict) -> Task:
    actor = ctx.actor
    ref = next_ref(platform, ctx.firm.id, "task", "T")
    task = Task(
        company_id=company.id,
        ref=ref,
        title=body["title"].strip(),
        description=body.get("description") or "",
        category=body.get("category") or "Integration",
        source_type=body.get("sourceType"),
        source_id=body.get("sourceId"),
        assignee=body.get("assignee") or actor,
        priority=body.get("priority") or "Medium",
        status="Open",
        due_date=date.fromisoformat(body["dueDate"]) if body.get("dueDate") else None,
        created_by=actor,
        synthetic_demo=False,
    )
    if not task.title:
        raise HTTPException(422, "Task title is required")
    with company_session(company) as ts:
        ts.add(task)
        if task.source_type == "finding" and task.source_id:
            f = _finding_in(ts, task.source_id)
            if f and f.status == "open":
                f.status = "actioned"
        ts.commit()
    if task.source_type == "opportunity" and task.source_id:
        o = platform.scalar(select(Opportunity).where(Opportunity.firm_id == ctx.firm.id, Opportunity.ref == task.source_id))
        if o and o.status in ("New", "Under review"):
            o.status = "Task created"
    log_activity(
        platform,
        ctx.firm.id,
        company.id,
        f"{actor.split(' ')[0]} created task {ref}{f' from {task.source_id}' if task.source_id else ''}.",
        "task",
    )
    platform.commit()
    return task


def update_task(platform: Session, ctx: FirmContext, ref: str, patch: dict) -> Task:
    actor = ctx.actor
    for company in ctx.companies:
        with company_session(company) as ts:
            t = ts.scalar(select(Task).where(Task.ref == ref))
            if t is None:
                continue
            if "status" in patch and patch["status"] not in TASK_STATUSES:
                raise HTTPException(422, f"Unknown task status {patch['status']!r}")
            for key, col in (
                ("title", "title"),
                ("description", "description"),
                ("assignee", "assignee"),
                ("priority", "priority"),
                ("status", "status"),
                ("outcome", "outcome"),
                ("outcomeNotes", "outcome_notes"),
            ):
                if key in patch and patch[key] is not None:
                    setattr(t, col, patch[key])
            if "dueDate" in patch:
                t.due_date = date.fromisoformat(patch["dueDate"]) if patch["dueDate"] else None
            if "realizedResult" in patch:
                t.realized_value = Decimal(str(patch["realizedResult"])) if patch["realizedResult"] not in (None, "") else None
            if patch.get("status") == "Complete":
                t.completed_at = now()
                if t.outcome != "Implemented":
                    t.realized_value = None
                if t.source_type == "opportunity" and t.source_id:
                    o = platform.scalar(select(Opportunity).where(Opportunity.firm_id == ctx.firm.id, Opportunity.ref == t.source_id))
                    if o is not None:
                        if t.outcome == "Implemented":
                            o.status, o.realized_value = "Realized", t.realized_value
                        elif t.outcome == "Benefit validated":
                            o.status = "Validated"
                        elif t.outcome == "No benefit found":
                            o.status = "Dismissed"
                realized = f" ({money2(t.realized_value)} realized)" if t.realized_value else ""
                log_activity(
                    platform, ctx.firm.id, company.id, f"{actor.split(' ')[0]} completed {ref} — {t.outcome or 'done'}{realized}.", "task"
                )
            elif patch.get("status"):
                log_activity(platform, ctx.firm.id, company.id, f"{ref} moved to {patch['status'].lower()}.", "task")
            ts.commit()
            platform.commit()
            return t
    raise HTTPException(404, "Task not found")


# ---- Opportunities & analysis -------------------------------------------------------


def set_opportunity_status(platform: Session, ctx: FirmContext, ref: str, status: str) -> Opportunity:
    if status not in OPP_STATUSES:
        raise HTTPException(422, f"Unknown opportunity status {status!r}")
    o = platform.scalar(select(Opportunity).where(Opportunity.firm_id == ctx.firm.id, Opportunity.ref == ref))
    if o is None:
        raise HTTPException(404, "Opportunity not found")
    o.status = status
    first = uuid.UUID(o.company_ids[0]) if o.company_ids else None
    log_activity(platform, ctx.firm.id, first, f"{o.ref} marked {status.lower()} by analyst.", "opportunity")
    platform.commit()
    return o


def _sku_rows(ctx: FirmContext) -> dict[str, list[dict]]:
    """Per SKU: one row per company that bought it in the period (units, spend, avg price)."""
    period = reporting_period()
    by_sku: dict[str, list[dict]] = {}
    for company in ctx.companies:
        with company_session(company) as ts:
            rows = ts.execute(
                select(
                    VendorPurchase.sku,
                    VendorPurchase.unit,
                    VendorPurchase.item_description,
                    func.sum(VendorPurchase.quantity),
                    func.sum(VendorPurchase.total_amount),
                    func.count(),
                )
                .where(VendorPurchase.purchase_date.between(period.start, period.end), VendorPurchase.sku != "")
                .group_by(VendorPurchase.sku, VendorPurchase.unit, VendorPurchase.item_description)
            ).all()
        for sku, unit, desc, units, spend, lines in rows:
            if not units:
                continue
            by_sku.setdefault(sku, []).append(
                {
                    "companyId": str(company.id),
                    "name": company.name,
                    "unit": unit or "unit",
                    "description": desc,
                    "units": float(units),
                    "spend": float(spend),
                    "lines": lines,
                    "unitPrice": round(float(spend) / float(units), 2),
                }
            )
    return by_sku


def run_portfolio_analysis(platform: Session, ctx: FirmContext) -> list[Opportunity]:
    """Deterministic purchasing rule: same SKU bought by ≥2 companies at
    different average unit prices. The gap × the higher payer's units is a
    modelled scenario, never a realized saving."""
    found: list[Opportunity] = []
    existing = platform.scalars(select(Opportunity).where(Opportunity.firm_id == ctx.firm.id, Opportunity.category == "Purchasing")).all()
    for sku, rows in sorted(_sku_rows(ctx).items()):
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r["unitPrice"])
        low = rows[0]
        for high in rows[1:]:
            gap = round(high["unitPrice"] - low["unitPrice"], 2)
            if gap <= 0:
                continue
            scenario = round(gap * high["units"], 2)
            unit = high["unit"]
            calculation = [
                f"{money2(high['unitPrice'])} - {money2(low['unitPrice'])} = {money2(gap)}/{unit}",
                f"{high['units']:,.0f} historical {unit}s × {money2(gap)} = {money2(scenario)} scenario",
            ]
            match = next(
                (o for o in existing if o.sku == sku and high["companyId"] in o.company_ids and low["companyId"] in o.company_ids), None
            )
            if match is not None:
                match.scenario_value = Decimal(str(scenario))
                match.calculation = calculation
                continue
            ref = next_ref(platform, ctx.firm.id, "opportunity", "OP", 3)
            opp = Opportunity(
                firm_id=ctx.firm.id,
                ref=ref,
                title=f"{high['name']} pays {money2(gap)}/{unit} more than {low['name']} for {sku}",
                category="Purchasing",
                sku=sku,
                company_ids=[high["companyId"], low["companyId"]],
                confidence=0.9,
                status="New",
                observed_fact=(
                    f"{high['name']} and {low['name']} both purchased SKU {sku} ({high['description']}). "
                    f"{high['name']}'s recorded average unit price was {money2(high['unitPrice'])} across {high['lines']} purchase lines; "
                    f"{low['name']}'s was {money2(low['unitPrice'])} across {low['lines']}."
                ),
                evidence=[
                    {"companyId": high["companyId"], "entity": "purchase", "sku": sku},
                    {"companyId": low["companyId"], "entity": "purchase", "sku": sku},
                ],
                calculation=calculation,
                potential_benefit=(
                    f"If {high['name']} were able to obtain {low['name']}'s historical unit rate on the same purchase volume, "
                    f"the modeled difference would be {money2(scenario)} over the reporting period."
                ),
                assumptions=[
                    "Contract terms unknown",
                    "Freight and delivery terms may differ",
                    "Rebate structures unknown",
                    "Future volume may differ from the period observed",
                ],
                recommended_action=(
                    f"Review both companies' supplier agreements for {sku} "
                    "and determine whether pricing can be consolidated under one account."
                ),
                scenario_value=Decimal(str(scenario)),
                generated_by="deterministic_rule",
                synthetic_demo=False,
            )
            platform.add(opp)
            existing.append(opp)
            found.append(opp)
            log_activity(
                platform,
                ctx.firm.id,
                uuid.UUID(high["companyId"]),
                f"Vista identified purchasing opportunity {ref} ({sku}).",
                "opportunity",
            )
    at = now()
    for c in ctx.companies:
        row = platform.get(FirmCompany, c.id)
        row.analysis_run_at = at
    log_activity(
        platform,
        ctx.firm.id,
        None,
        f"Portfolio analysis completed across {len(ctx.companies)} companies; "
        f"{len(found)} new opportunit{'y' if len(found) == 1 else 'ies'}.",
        "analysis",
        at,
    )
    platform.commit()
    return found


# ---- Finding triage ------------------------------------------------------------------

FINDING_STATUSES = {"Open": "open", "Reviewed": "reviewed", "Actioned": "actioned", "Dismissed": "dismissed"}


def _finding_in(ts: Session, ref: str) -> Finding | None:
    """A ledger finding by its display ref (F-012) or its uuid."""
    if _is_uuid(ref):
        return ts.get(Finding, uuid.UUID(ref))
    return ts.scalar(select(Finding).where(Finding.ref == ref))


def _finding_scopes(platform: Session, ctx: FirmContext) -> list[tuple[uuid.UUID | None, str]]:
    """(company id, schema) for every tenant the firm's findings live in: each company
    tenant plus the firm's home tenant, where the Sector Merger writes."""
    scopes: list[tuple[uuid.UUID | None, str]] = [(c.id, c.schema) for c in ctx.companies]
    home = platform.get(Tenant, ctx.firm.home_tenant_id)
    if home is not None:
        scopes.append((None, home.schema_name))
    return scopes


def set_finding_status(platform: Session, ctx: FirmContext, ref: str, status: str) -> dict:
    """Triage is the analyst's only write to a finding, and it lands on the same row the
    company workspace, the Sector Merger and the recorder read. Dismissed findings are
    invisible to downstream agents (A2A contract)."""
    if status not in FINDING_STATUSES:
        raise HTTPException(422, f"Unknown finding status {status!r}")
    from vista.portfolio import ledger

    for company_id, schema in _finding_scopes(platform, ctx):
        with tenant_session(schema) as ts:
            f = _finding_in(ts, ref)
            if f is None:
                continue
            f.status = FINDING_STATUSES[status]
            ts.flush()
            scope_slug = next((c.slug for c in ctx.companies if c.id == company_id), ctx.firm.slug)
            view = ledger.finding_view(f, ledger.agent_id(f.agent_key or "file_reviewer", scope_slug), company_id)
            ts.commit()
        log_activity(platform, ctx.firm.id, company_id, f"Finding {view['id']} {status.lower()} by analyst.", "finding")
        platform.commit()
        return view
    raise HTTPException(404, "Finding not found")


# ---- Import exceptions after approval -------------------------------------------------


def resolve_exception(platform: Session, ctx: FirmContext, company: CompanyRef, exception_id: str, decision: str) -> ImportException:
    with company_session(company) as ts:
        x = (
            ts.get(ImportException, uuid.UUID(exception_id))
            if _is_uuid(exception_id)
            else ts.scalar(select(ImportException).where(ImportException.ref == exception_id))
        )
        if x is None:
            raise HTTPException(404, "Exception not found")
        detail = x.detail or {}
        if decision not in detail.get("actions", []):
            raise HTTPException(422, f"{decision!r} is not an allowed resolution")
        x.status, x.resolution, x.resolved_by, x.resolved_at = "resolved", decision, ctx.principal.user_id, now()
        if detail.get("dataset") == "vendors" and decision == "Match" and detail.get("matchVendor"):
            for v in ts.scalars(select(Vendor).where(Vendor.source_name == detail["left"])):
                v.normalized_name = detail["matchVendor"]
        ts.commit()
    log_activity(
        platform,
        ctx.firm.id,
        company.id,
        f"Import exception {x.ref} resolved: {decision} ({detail.get('left')} / {detail.get('right')}).",
        "import",
    )
    platform.commit()
    return x


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False
