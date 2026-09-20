"""Tier-3 evaluation results as a ledger: `make eval` (or any caller with the score
JSON) records one row per scoring; the dashboard reads the latest per phase/scope.
Quality (precision/recall vs the answer key) is kept apart from spend and outcome
metrics on purpose — it measures the agent, not the business."""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from vista.agents.keys import AGENT_KEYS
from vista.auth import Principal, admin_principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import EvalRun

router = APIRouter(tags=["evals"])

PHASES = ("discover", "execute", "analyze")
AGENT_KEY_BY_PHASE = {"discover": "file_reviewer", "execute": "file_reviewer", "analyze": "sector_merger"}


class EvalScoreIn(BaseModel):
    tp: int = 0
    fp: int = 0
    fn: int = 0
    trap_hits: int = 0
    matched: list = Field(default_factory=list)
    missed: list[str] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)


class EvalCreate(BaseModel):
    phase: str
    sector: str | None = None
    company: str | None = None
    division: str | None = None
    model: str | None = None
    predictions: int = 0
    calls: int = 0
    cost_usd: Decimal = Decimal(0)
    score: EvalScoreIn


class EvalOut(BaseModel):
    id: uuid.UUID
    phase: str
    agent_key: str
    sector: str | None
    company: str | None
    division: str | None
    model: str | None
    predictions: int
    tp: int
    fp: int
    fn: int
    trap_hits: int
    precision: float
    recall: float
    calls: int
    cost_usd: Decimal
    detail: dict
    created_at: datetime

    model_config = {"from_attributes": True}


def build_eval_run(body: EvalCreate) -> EvalRun:
    if body.phase not in PHASES:
        raise HTTPException(422, f"phase must be one of {PHASES}")
    if body.phase == "analyze" and not body.sector:
        raise HTTPException(422, "analyze evals need a sector")
    if body.phase != "analyze" and not body.company:
        raise HTTPException(422, f"{body.phase} evals need a company")
    s = body.score
    denom_p, denom_r = s.tp + s.fp, s.tp + s.fn
    return EvalRun(
        phase=body.phase,
        agent_key=AGENT_KEY_BY_PHASE[body.phase],
        sector=body.sector,
        company=body.company,
        division=body.division,
        model=body.model,
        predictions=body.predictions,
        tp=s.tp,
        fp=s.fp,
        fn=s.fn,
        trap_hits=s.trap_hits,
        precision=s.tp / denom_p if denom_p else 0.0,
        recall=s.tp / denom_r if denom_r else 0.0,
        calls=body.calls,
        cost_usd=body.cost_usd,
        detail={"matched": s.matched, "missed": s.missed, "unmatched": s.unmatched},
    )


@router.post("/evals", response_model=EvalOut, status_code=201)
def record_eval(body: EvalCreate, principal: Principal = Depends(admin_principal)) -> EvalOut:
    row = build_eval_run(body)
    with tenant_session(principal.tenant_schema) as session:
        session.add(row)
        session.flush()
        session.refresh(row)  # server-side created_at, while the search_path is still set
        out = EvalOut.model_validate(row)
        session.commit()
        return out


@router.get("/evals", response_model=list[EvalOut])
def list_evals(
    phase: str | None = None,
    agent_key: str | None = None,
    sector: str | None = None,
    company: str | None = None,
    latest: bool = False,
    limit: int = 50,
    principal: Principal = Depends(current_principal),
) -> list[EvalOut]:
    """Newest first. `latest=true` keeps only the most recent row per
    (phase, sector, company, division) so the dashboard gets one line per scope."""
    if agent_key is not None and agent_key not in AGENT_KEYS:
        raise HTTPException(422, "unknown agent_key")
    q = select(EvalRun).order_by(EvalRun.created_at.desc())
    if phase:
        q = q.where(EvalRun.phase == phase)
    if agent_key:
        q = q.where(EvalRun.agent_key == agent_key)
    if sector:
        q = q.where(EvalRun.sector == sector)
    if company:
        q = q.where(EvalRun.company == company)
    with tenant_session(principal.tenant_schema) as session:
        rows = session.scalars(q if latest else q.limit(min(limit, 200))).all()
    if latest:
        seen: set[tuple] = set()
        kept = []
        for r in rows:
            scope = (r.phase, r.sector, r.company, r.division)
            if scope not in seen:
                seen.add(scope)
                kept.append(r)
        rows = kept[: min(limit, 200)]
    return [EvalOut.model_validate(r) for r in rows]
