import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import FindingOut, FindingPatch
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import AgentRun, Finding
from vista.permissions import require_deal_role, visible_deal_clause

router = APIRouter(tags=["findings"])

FINDING_STATUSES = {"open", "reviewed", "dismissed", "actioned"}


def _out(f: Finding) -> FindingOut:
    return FindingOut(
        id=f.id,
        run_id=f.run_id,
        employee_id=f.employee_id,
        agent_id=f.agent_id,
        kind=f.kind,
        title=f.title,
        detail=f.detail,
        evidence=f.evidence,
        status=f.status,
        created_at=f.created_at,
        company=f.company,
        agent_key=f.agent_key,
    )


@router.get("/findings", response_model=list[FindingOut])
def list_findings(
    status: str | None = None,
    employee_id: uuid.UUID | None = None,
    kind: str | None = None,
    company: str | None = None,
    agent_key: str | None = None,
    run_id: uuid.UUID | None = None,
    deal_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
    principal: Principal = Depends(current_principal),
) -> list[FindingOut]:
    """Findings newest first, scoped like /runs: non-admins see findings of
    portfolio-wide runs plus runs on deals they belong to."""
    limit = max(1, min(limit, 1000))
    query = (
        select(Finding)
        .join(AgentRun, AgentRun.id == Finding.run_id)
        .order_by(Finding.created_at.desc())
        .limit(limit)
        .offset(max(0, offset))
    )
    for column, value in (
        (Finding.status, status),
        (Finding.employee_id, employee_id),
        (Finding.kind, kind),
        (Finding.company, company),
        (Finding.agent_key, agent_key),
        (Finding.run_id, run_id),
        (AgentRun.deal_id, deal_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if since is not None:
        query = query.where(Finding.created_at >= since)
    with tenant_session(principal.tenant_schema) as session:
        if deal_id is not None:
            require_deal_role(session, deal_id, principal.user_id, "viewer")
        elif principal.role != "admin":
            query = query.where(visible_deal_clause(AgentRun.deal_id, principal.user_id))
        return [_out(f) for f in session.scalars(query).all()]


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def update_finding(finding_id: uuid.UUID, body: FindingPatch, principal: Principal = Depends(current_principal)) -> FindingOut:
    if body.status not in FINDING_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(FINDING_STATUSES)}")
    with tenant_session(principal.tenant_schema) as session:
        finding = session.get(Finding, finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        run_deal = session.scalar(select(AgentRun.deal_id).where(AgentRun.id == finding.run_id))
        if run_deal is not None:
            require_deal_role(session, run_deal, principal.user_id, "member")
        finding.status = body.status
        session.commit()
        return _out(finding)
