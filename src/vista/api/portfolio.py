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

TASK_STATUSES = {"Open", "In progress", "Done"}
TASK_PRIORITIES = {"High", "Medium", "Low"}
OPPORTUNITY_STATUSES = {"Open", "In review", "Realized", "Dismissed"}


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

    Metrics come from the newest committed import batch for the deal; a company
    with no committed import reports zeros rather than a guess.
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
            if b.status == "committed" and b.deal_id not in newest:
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


# Ingestion classifies insurance-broking exports; the portfolio workspace speaks
# in customers/vendors. Anything without a source kind is reported as unsourced
# rather than rendered as zero.
KIND_TO_PORTFOLIO = {
    "clients": "customers",
    "invoices": "invoices",
    "carriers": "vendors",
    "policies": "policies",
    "commissions": "commissions",
}
UNSOURCED_KINDS = ("subscriptions", "purchases")


@router.get("/companies/{deal_id}", response_model=PortfolioCompanyDetail)
def company_detail(deal_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> PortfolioCompanyDetail:
    with tenant_session(principal.tenant_schema) as session:
        deal = session.get(Deal, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="company not found")
        batch = session.scalars(
            select(ImportBatch)
            .where(ImportBatch.deal_id == deal_id, ImportBatch.status == "committed")
            .order_by(ImportBatch.created_at.desc())
            .limit(1)
        ).first()
        if batch is None:
            return PortfolioCompanyDetail(id=deal.id, name=deal.name, unsourced=[*KIND_TO_PORTFOLIO.values(), *UNSOURCED_KINDS])

        buckets: dict[str, list] = {name: [] for name in KIND_TO_PORTFOLIO.values()}
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
        present = {t.get("kind") for t in (batch.tables or [])}
        unsourced = [name for kind, name in KIND_TO_PORTFOLIO.items() if kind not in present]
        return PortfolioCompanyDetail(
            id=deal.id,
            name=deal.name,
            as_of=batch.as_of,
            batch_id=batch.id,
            customers=buckets["customers"],
            invoices=buckets["invoices"],
            vendors=buckets["vendors"],
            policies=buckets["policies"],
            commissions=buckets["commissions"],
            subscriptions=[],
            purchases=[],
            unsourced=[*unsourced, *UNSOURCED_KINDS],
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
            task.completed_at = datetime.now(UTC) if body.status == "Done" else None
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
