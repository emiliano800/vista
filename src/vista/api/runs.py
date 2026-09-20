import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.agents.keys import agent_key_for
from vista.api.schemas import RunCreate, RunEventOut, RunOut
from vista.auth import Principal, current_principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import AgentRun, AgentRunEvent, Deal, Document
from vista.permissions import require_deal_role, visible_deal_clause

router = APIRouter(tags=["runs"])


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(body: RunCreate, principal: Principal = Depends(current_principal)) -> RunOut:
    with tenant_session(principal.tenant_schema) as session:
        require_deal_role(session, body.deal_id, principal.user_id, "member")
        if body.document_id is not None:
            doc = session.get(Document, body.document_id)
            if doc is None or doc.deal_id != body.deal_id:
                raise HTTPException(status_code=404, detail="document not found in deal")
        run = AgentRun(
            job_id=uuid.uuid4(),  # placeholder, replaced after enqueue
            deal_id=body.deal_id,
            document_id=body.document_id,
            requested_by=principal.user_id,
            agent_key=agent_key_for("deal_analysis"),
            company=session.scalar(select(Deal.name).where(Deal.id == body.deal_id)),
        )
        session.add(run)
        session.flush()
        with platform_session() as psession:
            job = enqueue(
                psession,
                tenant_id=principal.tenant_id,
                kind="agent_run",
                payload={"run_id": str(run.id)},
                idempotency_key=body.idempotency_key,
            )
            psession.commit()
        run.job_id = job.id
        session.commit()
        return _run_out(run, [])


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    deal_id: uuid.UUID | None = None,
    run_type: str | None = None,
    agent_key: str | None = None,
    company: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(current_principal),
) -> list[RunOut]:
    """Recent runs, newest first. Events are omitted here; fetch one run for those.
    Non-admins see portfolio-wide runs plus runs on deals they belong to."""
    limit = max(1, min(limit, 200))
    query = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).offset(max(0, offset))
    for column, value in (
        (AgentRun.deal_id, deal_id),
        (AgentRun.run_type, run_type),
        (AgentRun.agent_key, agent_key),
        (AgentRun.company, company),
        (AgentRun.status, status),
    ):
        if value is not None:
            query = query.where(column == value)
    if since is not None:
        query = query.where(AgentRun.created_at >= since)
    with tenant_session(principal.tenant_schema) as session:
        if deal_id is not None:
            require_deal_role(session, deal_id, principal.user_id, "viewer")
        elif principal.role != "admin":
            query = query.where(visible_deal_clause(AgentRun.deal_id, principal.user_id))
        return [_run_out(run, []) for run in session.scalars(query).all()]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> RunOut:
    with tenant_session(principal.tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.deal_id is not None:
            require_deal_role(session, run.deal_id, principal.user_id, "viewer")
        events = session.scalars(select(AgentRunEvent).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq)).all()
        return _run_out(run, events)


def _run_out(run: AgentRun, events: list[AgentRunEvent]) -> RunOut:
    return RunOut(
        id=run.id,
        run_type=run.run_type,
        deal_id=run.deal_id,
        employee_agent_id=run.employee_agent_id,
        document_id=run.document_id,
        status=run.status,
        created_at=run.created_at,
        finished_at=run.finished_at,
        started_at=run.started_at,
        company=run.company,
        division=run.division,
        sector=run.sector,
        agent_key=run.agent_key,
        recording_id=run.recording_id,
        error=run.error,
        events=[RunEventOut(seq=e.seq, event_type=e.event_type, data=e.data, created_at=e.created_at) for e in events],
    )
