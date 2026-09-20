"""Synthetic-company endpoints: the only data source the agent suite reads today."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from vista.agents import synthetic
from vista.api.schemas import RunOut
from vista.auth import Principal, admin_principal, current_principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import AgentRun

router = APIRouter(tags=["synthetic"])


class SyntheticCompanyOut(BaseModel):
    slug: str
    name: str
    short: str
    sector: str
    tier: str
    divisions: list[str]


class DiscoveryCreate(BaseModel):
    company: str  # short name or slug
    division: str


@router.get("/synthetic/companies", response_model=list[SyntheticCompanyOut])
def list_synthetic_companies(principal: Principal = Depends(current_principal)) -> list[SyntheticCompanyOut]:
    return [SyntheticCompanyOut(**c.__dict__, divisions=synthetic.divisions(c)) for c in synthetic.companies()]


@router.post("/synthetic/discovery", response_model=RunOut, status_code=201)
def trigger_synthetic_discovery(body: DiscoveryCreate, principal: Principal = Depends(admin_principal)) -> RunOut:
    """Queue a File Reviewer run over one division of one synthetic company."""
    try:
        company = synthetic.company(body.company)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown synthetic company") from None
    if body.division not in synthetic.divisions(company):
        raise HTTPException(status_code=404, detail="unknown division for company")
    with tenant_session(principal.tenant_schema) as session:
        run = AgentRun(job_id=uuid.uuid4(), run_type="synthetic_discovery", requested_by=principal.user_id)
        session.add(run)
        session.flush()
        with platform_session() as psession:
            job = enqueue(
                psession,
                tenant_id=principal.tenant_id,
                kind="synthetic_discovery",
                payload={"run_id": str(run.id), "company": company.slug, "division": body.division},
            )
            psession.commit()
        run.job_id = job.id
        session.commit()
        return RunOut(
            id=run.id,
            run_type=run.run_type,
            deal_id=None,
            employee_agent_id=None,
            document_id=None,
            status=run.status,
            created_at=run.created_at,
            finished_at=None,
        )
