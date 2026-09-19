import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import RunCreate, RunEventOut, RunOut
from vista.auth import Principal, current_principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import AgentRun, AgentRunEvent, Document
from vista.permissions import require_deal_role

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


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> RunOut:
    with tenant_session(principal.tenant_schema) as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.deal_id is not None:
            require_deal_role(session, run.deal_id, principal.user_id, "viewer")
        events = session.scalars(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run_id).order_by(AgentRunEvent.seq)
        ).all()
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
        events=[
            RunEventOut(seq=e.seq, event_type=e.event_type, data=e.data, created_at=e.created_at)
            for e in events
        ],
    )
