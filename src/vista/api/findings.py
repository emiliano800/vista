import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import FindingOut, FindingPatch
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import Finding

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
    )


@router.get("/findings", response_model=list[FindingOut])
def list_findings(
    status: str | None = None,
    employee_id: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
) -> list[FindingOut]:
    query = select(Finding).order_by(Finding.created_at.desc())
    if status is not None:
        query = query.where(Finding.status == status)
    if employee_id is not None:
        query = query.where(Finding.employee_id == employee_id)
    with tenant_session(principal.tenant_schema) as session:
        return [_out(f) for f in session.scalars(query).all()]


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def update_finding(finding_id: uuid.UUID, body: FindingPatch, principal: Principal = Depends(current_principal)) -> FindingOut:
    if body.status not in FINDING_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(FINDING_STATUSES)}")
    with tenant_session(principal.tenant_schema) as session:
        finding = session.get(Finding, finding_id)
        if finding is None:
            raise HTTPException(status_code=404, detail="finding not found")
        finding.status = body.status
        session.commit()
        return _out(finding)
