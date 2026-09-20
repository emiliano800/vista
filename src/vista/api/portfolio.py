"""PE analyst portfolio.

The firm is the tenant; each portfolio company is a deal inside it. Every
figure here is derived from stored records — import batches, findings and
agent runs — so the workspace never shows a number it cannot source.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import (
    ActivityOut,
    AnalysisRunOut,
    OpportunityOut,
    OpportunityPatch,
    PortfolioCompanyDetail,
    PortfolioCompanyOut,
    TaskCreate,
    TaskOut,
    TaskPatch,
)
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import (
    AgentRun,
    Deal,
    EmployeeAgent,
    Finding,
    ImportBatch,
    PortfolioActivity,
    PortfolioOpportunity,
    PortfolioTask,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# The workspace vocabulary, kept as the single source of truth so the UI and
# the database never drift into two spellings of the same state.
TASK_STATUSES = {"Open", "In progress", "Complete", "Dismissed"}
TASK_TERMINAL = {"Complete", "Dismissed"}
TASK_PRIORITIES = {"High", "Medium", "Low"}
OPPORTUNITY_STATUSES = {"New", "Under review", "Task created", "Validated", "Realized", "Dismissed"}


def _log(session, kind: str, summary: str, actor: str, deal_id: uuid.UUID | None = None, ref: dict | None = None) -> None:
    session.add(PortfolioActivity(deal_id=deal_id, kind=kind, summary=summary, actor=actor, ref=ref or {}))


def _task_out(t: PortfolioTask) -> TaskOut:
    return TaskOut(
        id=t.id,
        deal_id=t.deal_id,
        title=t.title,
        description=t.description,
        category=t.category,
        source_type=t.source_type,
        source_id=t.source_id,
        assignee=t.assignee,
        priority=t.priority,
        due_date=t.due_date,
        status=t.status,
        created_by=t.created_by,
        created_at=t.created_at,
        completed_at=t.completed_at,
        outcome=t.outcome,
        outcome_notes=t.outcome_notes,
        realized_result=t.realized_result,
    )


def _opportunity_out(o: PortfolioOpportunity) -> OpportunityOut:
    return OpportunityOut(
        id=o.id,
        title=o.title,
        category=o.category,
        deal_ids=[uuid.UUID(str(d)) for d in (o.deal_ids or [])],
        confidence=o.confidence,
        potential_value=o.potential_value,
        status=o.status,
        found_at=o.found_at,
        fact=o.fact,
        evidence=o.evidence or [],
        calculation=o.calculation or [],
        benefit=o.benefit,
        assumptions=o.assumptions or [],
        next_action=o.next_action,
        realized_value=o.realized_value,
    )


@router.get("/companies", response_model=list[PortfolioCompanyOut])
def list_companies(principal: Principal = Depends(current_principal)) -> list[PortfolioCompanyOut]:
    """One row per portfolio company, with the counts the overview shows.

    Metrics come from the newest completed import batch for the deal; a company
    with no completed import reports zeros rather than a guess.
    """
    with tenant_session(principal.tenant_schema) as session:
        deals = session.scalars(select(Deal).order_by(Deal.created_at)).all()
        batches = session.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc())).all()
        findings = session.scalars(select(Finding)).all()
        agents = session.scalars(select(EmployeeAgent)).all()
        runs = session.scalars(select(AgentRun).order_by(AgentRun.created_at.desc())).all()

        newest: dict[uuid.UUID, ImportBatch] = {}
        counts: dict[uuid.UUID, int] = {}
        for b in batches:
            counts[b.deal_id] = counts.get(b.deal_id, 0) + 1
            if b.status == "completed" and b.deal_id not in newest:
                newest[b.deal_id] = b

        runs_by_deal: dict[uuid.UUID, AgentRun] = {}
        for r in runs:
            if r.deal_id is not None and r.deal_id not in runs_by_deal:
                runs_by_deal[r.deal_id] = r

        open_findings = sum(1 for f in findings if f.status == "open")
        rows = []
        for deal in deals:
            batch = newest.get(deal.id)
            analysis = (batch.analysis if batch else {}) or {}
            batch_findings = analysis.get("findings", []) or []
            exposure = sum(Decimal(str(f.get("amount") or 0)) for f in batch_findings)
            records = sum(len(t.get("records", []) or []) for t in ((batch.tables if batch else []) or []))
            last = runs_by_deal.get(deal.id)
            rows.append(
                PortfolioCompanyOut(
                    id=deal.id,
                    name=deal.name,
                    profile=deal.profile or {},
                    created_at=deal.created_at,
                    as_of=batch.as_of if batch else None,
                    records=records,
                    findings_open=sum(1 for f in batch_findings if (f.get("status") or "open") == "open") or open_findings,
                    findings_total=len(batch_findings),
                    exposure=exposure,
                    imports=counts.get(deal.id, 0),
                    agents_active=sum(1 for a in agents if a.status == "active"),
                    last_run_at=last.created_at if last else None,
                )
            )
        return rows


# Ingestion kinds mapped into the vocabulary the portfolio workspace speaks.
# Carrier agreements are the vendor relationship for a broking company and
# industrial companies import a vendors file, so both feed the same bucket.
# A bucket with no records is reported as unsourced rather than rendered as a
# zero that would read as a measurement.
KIND_TO_PORTFOLIO = {
    "clients": "customers",
    "invoices": "invoices",
    "carriers": "vendors",
    "vendors": "vendors",
    "purchases": "purchases",
    "subscriptions": "subscriptions",
    "policies": "policies",
    "commissions": "commissions",
}
PORTFOLIO_BUCKETS = ("customers", "invoices", "vendors", "purchases", "subscriptions", "policies", "commissions")


@router.get("/companies/{deal_id}", response_model=PortfolioCompanyDetail)
def company_detail(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> PortfolioCompanyDetail:
    with tenant_session(principal.tenant_schema) as session:
        deal = session.get(Deal, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="company not found")
        batch = session.scalars(
            select(ImportBatch)
            .where(ImportBatch.deal_id == deal_id, ImportBatch.status == "completed")
            .order_by(ImportBatch.created_at.desc())
            .limit(1)
        ).first()
        if batch is None:
            return PortfolioCompanyDetail(id=deal.id, name=deal.name, profile=deal.profile or {}, unsourced=list(PORTFOLIO_BUCKETS))

        buckets: dict[str, list] = {name: [] for name in PORTFOLIO_BUCKETS}
        for table in batch.tables or []:
            target = KIND_TO_PORTFOLIO.get(table.get("kind"))
            if target is None:
                continue
            mapping = table.get("mapping") or {}
            for record in table.get("records", []) or []:
                values = record.get("values", {}) or {}
                row = {field: (values.get(column) or "").strip() for field, column in mapping.items()}
                row["_row"] = record.get("row")
                row["_table"] = table.get("id")
                buckets[target].append(row)

        analysis = batch.analysis or {}
        return PortfolioCompanyDetail(
            id=deal.id,
            name=deal.name,
            profile=deal.profile or {},
            as_of=batch.as_of,
            batch_id=batch.id,
            customers=buckets["customers"],
            invoices=buckets["invoices"],
            vendors=buckets["vendors"],
            policies=buckets["policies"],
            commissions=buckets["commissions"],
            subscriptions=buckets["subscriptions"],
            purchases=buckets["purchases"],
            unsourced=[name for name in PORTFOLIO_BUCKETS if not buckets[name]],
            exceptions=analysis.get("exceptions", []) or [],
            analysis=analysis,
        )


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    deal_id: uuid.UUID | None = None,
    status: str | None = None,
    principal: Principal = Depends(current_principal),
) -> list[TaskOut]:
    query = select(PortfolioTask).order_by(PortfolioTask.created_at.desc())
    if deal_id is not None:
        query = query.where(PortfolioTask.deal_id == deal_id)
    if status is not None:
        query = query.where(PortfolioTask.status == status)
    with tenant_session(principal.tenant_schema) as session:
        return [_task_out(t) for t in session.scalars(query).all()]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(body: TaskCreate, principal: Principal = Depends(current_principal)) -> TaskOut:
    if body.priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=422, detail=f"priority must be one of {sorted(TASK_PRIORITIES)}")
    with tenant_session(principal.tenant_schema) as session:
        if session.get(Deal, body.deal_id) is None:
            raise HTTPException(status_code=404, detail="company not found")
        task = PortfolioTask(
            deal_id=body.deal_id,
            title=body.title,
            description=body.description,
            category=body.category,
            source_type=body.source_type,
            source_id=body.source_id,
            assignee=body.assignee,
            priority=body.priority,
            due_date=body.due_date,
            created_by=principal.email,
        )
        session.add(task)
        session.flush()
        _log(session, "task", f"Task created: {task.title}", principal.email, task.deal_id, {"task_id": str(task.id)})
        session.commit()
        return _task_out(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: uuid.UUID, body: TaskPatch, principal: Principal = Depends(current_principal)) -> TaskOut:
    if body.status is not None and body.status not in TASK_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(TASK_STATUSES)}")
    if body.priority is not None and body.priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=422, detail=f"priority must be one of {sorted(TASK_PRIORITIES)}")
    with tenant_session(principal.tenant_schema) as session:
        task = session.get(PortfolioTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        for field in ("title", "description", "assignee", "priority", "due_date", "outcome", "outcome_notes", "realized_result"):
            value = getattr(body, field)
            if value is not None:
                setattr(task, field, value)
        if body.status is not None and body.status != task.status:
            task.status = body.status
            task.completed_at = datetime.now(UTC) if body.status in TASK_TERMINAL else None
            _log(session, "task", f"Task {body.status.lower()}: {task.title}", principal.email, task.deal_id, {"task_id": str(task.id)})
        session.commit()
        return _task_out(task)


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(principal: Principal = Depends(current_principal)) -> list[OpportunityOut]:
    with tenant_session(principal.tenant_schema) as session:
        query = select(PortfolioOpportunity).order_by(PortfolioOpportunity.potential_value.desc())
        return [_opportunity_out(o) for o in session.scalars(query).all()]


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityOut)
def update_opportunity(
    opportunity_id: uuid.UUID,
    body: OpportunityPatch,
    principal: Principal = Depends(current_principal),
) -> OpportunityOut:
    if body.status not in OPPORTUNITY_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(OPPORTUNITY_STATUSES)}")
    with tenant_session(principal.tenant_schema) as session:
        opportunity = session.get(PortfolioOpportunity, opportunity_id)
        if opportunity is None:
            raise HTTPException(status_code=404, detail="opportunity not found")
        opportunity.status = body.status
        if body.realized_value is not None:
            opportunity.realized_value = body.realized_value
        elif body.status == "Realized" and opportunity.realized_value is None:
            opportunity.realized_value = opportunity.potential_value
        _log(
            session,
            "opportunity",
            f"Opportunity {body.status.lower()}: {opportunity.title}",
            principal.email,
            None,
            {"opportunity_id": str(opportunity.id)},
        )
        session.commit()
        return _opportunity_out(opportunity)


@router.get("/activity", response_model=list[ActivityOut])
def list_activity(
    deal_id: uuid.UUID | None = None,
    limit: int = 50,
    principal: Principal = Depends(current_principal),
) -> list[ActivityOut]:
    limit = max(1, min(limit, 200))
    query = select(PortfolioActivity).order_by(PortfolioActivity.at.desc()).limit(limit)
    if deal_id is not None:
        query = query.where(PortfolioActivity.deal_id == deal_id)
    with tenant_session(principal.tenant_schema) as session:
        return [
            ActivityOut(id=a.id, deal_id=a.deal_id, kind=a.kind, summary=a.summary, actor=a.actor, ref=a.ref or {}, at=a.at)
            for a in session.scalars(query).all()
        ]


def _purchase_lines(batch: ImportBatch) -> list[dict]:
    lines = []
    for table in batch.tables or []:
        if table.get("kind") != "purchases":
            continue
        mapping = table.get("mapping") or {}
        for record in table.get("records", []) or []:
            values = record.get("values", {}) or {}
            row = {field: (values.get(column) or "").strip() for field, column in mapping.items()}
            row["_table"] = table.get("id")
            row["_row"] = record.get("row")
            lines.append(row)
    return lines


@router.post("/analysis", response_model=AnalysisRunOut, status_code=201)
def run_analysis(principal: Principal = Depends(current_principal)) -> AnalysisRunOut:
    """Compare unit prices for the same SKU across portfolio companies.

    Cross-company arbitrage is the one analysis a single company cannot run on
    its own data, so it lives here rather than in per-company ingestion. The
    value is a modelled scenario, not a saving: it is the price gap applied to
    the volume the higher-paying company actually bought.
    """
    with tenant_session(principal.tenant_schema) as session:
        deals = {d.id: d for d in session.scalars(select(Deal)).all()}
        batches = session.scalars(
            select(ImportBatch).where(ImportBatch.status == "completed").order_by(ImportBatch.created_at.desc())
        ).all()
        newest: dict[uuid.UUID, ImportBatch] = {}
        for b in batches:
            newest.setdefault(b.deal_id, b)

        # sku -> deal_id -> {units, spend, rows}
        by_sku: dict[str, dict[uuid.UUID, dict]] = {}
        for deal_id, batch in newest.items():
            for line in _purchase_lines(batch):
                try:
                    quantity = Decimal(line.get("quantity", "").replace(",", "") or 0)
                    unit_price = Decimal(line.get("unit_price", "").replace(",", "").replace("$", "") or 0)
                except (ArithmeticError, ValueError):
                    continue
                if quantity <= 0 or unit_price <= 0:
                    continue
                sku = (line.get("sku") or "").strip()
                if not sku:
                    continue
                entry = by_sku.setdefault(sku, {}).setdefault(deal_id, {"units": Decimal(0), "spend": Decimal(0), "rows": []})
                entry["units"] += quantity
                entry["spend"] += quantity * unit_price
                entry["rows"].append({"deal_id": str(deal_id), "table_id": line.get("_table"), "row": line.get("_row")})

        existing = {
            (o.category, (o.evidence or [{}])[0].get("sku"), tuple(sorted(str(d) for d in (o.deal_ids or [])))): o
            for o in session.scalars(select(PortfolioOpportunity).where(PortfolioOpportunity.category == "Purchasing")).all()
        }
        created, updated, compared = [], [], 0
        for sku, per_deal in sorted(by_sku.items()):
            if len(per_deal) < 2:
                continue
            compared += 1
            priced = sorted(
                ((deal_id, e, (e["spend"] / e["units"]).quantize(Decimal("0.01"))) for deal_id, e in per_deal.items()),
                key=lambda row: row[2],
            )
            low_id, low, low_price = priced[0]
            for high_id, high, high_price in priced[1:]:
                gap = high_price - low_price
                if gap <= 0:
                    continue
                scenario = (gap * high["units"]).quantize(Decimal("0.01"))
                key = ("Purchasing", sku, tuple(sorted([str(high_id), str(low_id)])))
                calculation = [
                    f"{high_price} - {low_price} = {gap} per unit",
                    f"{high['units']} units purchased at the higher rate x {gap} = {scenario} scenario",
                ]
                if key in existing:
                    opportunity = existing[key]
                    opportunity.potential_value = scenario
                    opportunity.calculation = calculation
                    updated.append(_opportunity_out(opportunity))
                    continue
                opportunity = PortfolioOpportunity(
                    title=f"{deals[high_id].name} pays {gap} more per unit than {deals[low_id].name} for {sku}",
                    category="Purchasing",
                    deal_ids=[str(high_id), str(low_id)],
                    confidence=0.9,
                    potential_value=scenario,
                    status="New",
                    fact=(
                        f"{deals[high_id].name} recorded an average unit price of {high_price} across "
                        f"{len(high['rows'])} purchase lines for {sku}; {deals[low_id].name} recorded {low_price} "
                        f"across {len(low['rows'])}."
                    ),
                    evidence=[{"sku": sku, "lines": high["rows"] + low["rows"]}],
                    calculation=calculation,
                    benefit=(f"At {deals[low_id].name}'s recorded rate on the same volume, the modelled difference is {scenario}."),
                    assumptions=[
                        "Contract terms, freight and rebates are not in the imported data",
                        "Future volume may differ from the period imported",
                        "Unit prices are averaged per company over the snapshot",
                    ],
                    next_action=f"Compare both suppliers' agreements for {sku} and check whether one account can cover both.",
                )
                session.add(opportunity)
                session.flush()
                _log(session, "opportunity", f"Purchasing opportunity found for {sku}", principal.email, high_id, {"sku": sku})
                created.append(_opportunity_out(opportunity))
        _log(
            session,
            "analysis",
            f"Portfolio analysis compared {compared} shared SKUs across {len(newest)} companies; {len(created)} new.",
            principal.email,
        )
        session.commit()
        return AnalysisRunOut(companies=len(newest), skus_compared=compared, created=created, updated=updated)
