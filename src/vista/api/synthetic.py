"""Synthetic-company endpoints: the only data source the agent suite reads today."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from vista.agents import analyze, synthetic
from vista.agents.keys import agent_key_for
from vista.api.runs import _run_out
from vista.api.schemas import RunOut
from vista.auth import Principal, admin_principal, current_principal
from vista.db import platform_session, tenant_session
from vista.jobs.queue import enqueue
from vista.models.tenant import AgentRun, Deal

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


class AnalyzeCreate(BaseModel):
    sector: str
    kinds: list[str] | None = None  # default: every kind the sector supports


def _queue_run(
    principal: Principal, run_type: str, payload: dict, company: synthetic.Company | None = None, sector: str | None = None
) -> RunOut:
    """A synthetic company is the Deal of the same name when the firm has one, so
    the run shows up under that company in the workspace."""
    with tenant_session(principal.tenant_schema) as session:
        run = AgentRun(
            job_id=uuid.uuid4(),
            run_type=run_type,
            requested_by=principal.user_id,
            agent_key=agent_key_for(run_type),
            company=company.short if company else None,
            division=payload.get("division"),
            sector=company.sector if company else sector,
        )
        if company is not None:
            run.deal_id = session.scalar(select(Deal.id).where(Deal.name == company.name))
        session.add(run)
        session.flush()
        with platform_session() as psession:
            job = enqueue(psession, tenant_id=principal.tenant_id, kind=run_type, payload={"run_id": str(run.id), **payload})
            psession.commit()
        run.job_id = job.id
        session.commit()
        return _run_out(run, [])


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
    return _queue_run(principal, "synthetic_discovery", {"company": company.slug, "division": body.division}, company=company)


@router.post("/synthetic/analyze", response_model=RunOut, status_code=201)
def trigger_synthetic_analyze(body: AnalyzeCreate, principal: Principal = Depends(admin_principal)) -> RunOut:
    """Queue a Sector Merger (Portfolio Analyst) run across every synthetic company in one sector."""
    if body.sector not in analyze.SECTOR_KINDS:
        raise HTTPException(status_code=404, detail=f"sector must be one of {sorted(analyze.SECTOR_KINDS)}")
    allowed = analyze.SECTOR_KINDS[body.sector]
    kinds = body.kinds or allowed
    if unknown := sorted(set(kinds) - set(allowed)):
        raise HTTPException(status_code=422, detail=f"kinds not supported for {body.sector}: {unknown}")
    return _queue_run(principal, "synthetic_analyze", {"sector": body.sector, "kinds": kinds}, sector=body.sector)
