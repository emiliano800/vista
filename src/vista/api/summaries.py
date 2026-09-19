import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import RunOut, SummaryOut
from vista.auth import Principal, admin_principal, current_principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import AgentRun, CompanySummary

router = APIRouter(tags=["summaries"])


def _out(s: CompanySummary) -> SummaryOut:
    return SummaryOut(
        id=s.id, run_id=s.run_id, content=s.content, stats=s.stats, created_at=s.created_at
    )


@router.post("/summaries", response_model=RunOut, status_code=201)
def trigger_summary(principal: Principal = Depends(admin_principal)) -> RunOut:
    """Trigger a company-wide summary run over all open findings."""
    with tenant_session(principal.tenant_schema) as session:
        run = AgentRun(
            job_id=uuid.uuid4(), run_type="company_summary", requested_by=principal.user_id
        )
        session.add(run)
        session.flush()
        with platform_session() as psession:
            job = enqueue(
                psession,
                tenant_id=principal.tenant_id,
                kind="company_summary",
                payload={"run_id": str(run.id)},
            )
            psession.commit()
        run.job_id = job.id
        session.commit()
        return RunOut(
            id=run.id, run_type=run.run_type, deal_id=None, employee_agent_id=None,
            document_id=None, status=run.status, created_at=run.created_at, finished_at=None,
        )


@router.get("/summaries", response_model=list[SummaryOut])
def list_summaries(principal: Principal = Depends(current_principal)) -> list[SummaryOut]:
    with tenant_session(principal.tenant_schema) as session:
        summaries = session.scalars(
            select(CompanySummary).order_by(CompanySummary.created_at.desc())
        ).all()
        return [_out(s) for s in summaries]


@router.get("/summaries/latest", response_model=SummaryOut)
def latest_summary(principal: Principal = Depends(current_principal)) -> SummaryOut:
    with tenant_session(principal.tenant_schema) as session:
        summary = session.scalars(
            select(CompanySummary).order_by(CompanySummary.created_at.desc()).limit(1)
        ).first()
        if summary is None:
            raise HTTPException(status_code=404, detail="no summaries yet")
        return _out(summary)
