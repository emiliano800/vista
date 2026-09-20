import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from vista.api.schemas import (
    AgentCreate,
    AgentOut,
    AgentPatch,
    EmployeeCreate,
    EmployeeOut,
    RunOut,
)
from vista.auth import Principal, admin_principal, current_principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.jobs.scheduler import SCHEDULE_INTERVALS
from vista.models.tenant import AgentRun, Deal, Employee, EmployeeAgent
from vista.permissions import require_agent_role

router = APIRouter(tags=["employees"])

AGENT_STATUSES = {"active", "paused"}


def _employee_out(e: Employee) -> EmployeeOut:
    return EmployeeOut(id=e.id, name=e.name, role_title=e.role_title, email=e.email, created_at=e.created_at)


def _agent_out(a: EmployeeAgent, e: Employee) -> AgentOut:
    return AgentOut(
        id=a.id,
        employee_id=e.id,
        deal_id=a.deal_id,
        employee_name=e.name,
        role_title=e.role_title,
        status=a.status,
        scopes=a.scopes,
        schedule=a.schedule,
        last_run_at=a.last_run_at,
        created_at=a.created_at,
    )


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(body: EmployeeCreate, principal: Principal = Depends(admin_principal)) -> EmployeeOut:
    with tenant_session(principal.tenant_schema) as session:
        employee = Employee(name=body.name, role_title=body.role_title, email=body.email)
        session.add(employee)
        session.commit()
        return _employee_out(employee)


@router.get("/employees", response_model=list[EmployeeOut])
def list_employees(principal: Principal = Depends(current_principal)) -> list[EmployeeOut]:
    with tenant_session(principal.tenant_schema) as session:
        return [_employee_out(e) for e in session.scalars(select(Employee)).all()]


@router.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(body: AgentCreate, principal: Principal = Depends(admin_principal)) -> AgentOut:
    if body.schedule not in SCHEDULE_INTERVALS:
        raise HTTPException(status_code=422, detail=f"schedule must be one of {sorted(SCHEDULE_INTERVALS)}")
    with tenant_session(principal.tenant_schema) as session:
        employee = session.get(Employee, body.employee_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="employee not found")
        existing = session.scalar(select(EmployeeAgent).where(EmployeeAgent.employee_id == body.employee_id))
        if existing is not None:
            raise HTTPException(status_code=409, detail="employee already has an agent")
        if body.deal_id is not None and session.get(Deal, body.deal_id) is None:
            raise HTTPException(status_code=404, detail="company not found")
        agent = EmployeeAgent(employee_id=body.employee_id, deal_id=body.deal_id, scopes=body.scopes, schedule=body.schedule)
        session.add(agent)
        session.commit()
        return _agent_out(agent, employee)


@router.get("/agents", response_model=list[AgentOut])
def list_agents(
    deal_id: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
) -> list[AgentOut]:
    query = select(EmployeeAgent, Employee).join(Employee, Employee.id == EmployeeAgent.employee_id)
    if deal_id is not None:
        query = query.where(EmployeeAgent.deal_id == deal_id)
    with tenant_session(principal.tenant_schema) as session:
        return [_agent_out(a, e) for a, e in session.execute(query).all()]


@router.patch("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: uuid.UUID, body: AgentPatch, principal: Principal = Depends(admin_principal)) -> AgentOut:
    with tenant_session(principal.tenant_schema) as session:
        agent = session.get(EmployeeAgent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        if body.status is not None:
            if body.status not in AGENT_STATUSES:
                raise HTTPException(status_code=422, detail=f"status must be one of {sorted(AGENT_STATUSES)}")
            agent.status = body.status
        if body.scopes is not None:
            agent.scopes = body.scopes
        if body.schedule is not None:
            if body.schedule not in SCHEDULE_INTERVALS:
                raise HTTPException(status_code=422, detail=f"schedule must be one of {sorted(SCHEDULE_INTERVALS)}")
            agent.schedule = body.schedule
        employee = session.get(Employee, agent.employee_id)
        session.commit()
        return _agent_out(agent, employee)


@router.post("/agents/{agent_id}/runs", response_model=RunOut, status_code=201)
def trigger_discovery_run(agent_id: uuid.UUID, principal: Principal = Depends(current_principal)) -> RunOut:
    """Trigger an on-demand discovery run for one employee agent."""
    with tenant_session(principal.tenant_schema) as session:
        agent = session.get(EmployeeAgent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        require_agent_role(session, principal, agent.deal_id)
        run = AgentRun(
            job_id=uuid.uuid4(),
            run_type="employee_discovery",
            employee_agent_id=agent.id,
            deal_id=agent.deal_id,
            requested_by=principal.user_id,
            agent_key="file_reviewer",
        )
        session.add(run)
        session.flush()
        with platform_session() as psession:
            job = enqueue(
                psession,
                tenant_id=principal.tenant_id,
                kind="employee_discovery",
                payload={"run_id": str(run.id)},
            )
            psession.commit()
        run.job_id = job.id
        session.commit()
        return RunOut(
            id=run.id,
            run_type=run.run_type,
            deal_id=None,
            employee_agent_id=agent.id,
            document_id=None,
            status=run.status,
            created_at=run.created_at,
            finished_at=None,
        )
