"""Fleet analytics for the four suite agents: throughput, outcomes, spend and quality
in one read, so the /agents page can render without stitching /runs, /findings and
/usage together client-side."""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from vista.agents.keys import AGENT_KEYS, AGENT_NAMES
from vista.api.evals import EvalOut
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import AgentRun, EvalRun, Finding, UsageEvent
from vista.permissions import visible_deal_clause

router = APIRouter(tags=["agents"])

WINDOW_DAYS = 30


class AgentFleetOut(BaseModel):
    agent_key: str
    name: str
    runs: int
    succeeded: int
    failed: int
    active: int  # queued + running + waiting
    last_run_at: datetime | None
    last_status: str | None
    avg_seconds: float | None
    findings_open: int
    findings_total: int
    cost_usd: Decimal
    cost_month_usd: Decimal
    tokens: int


class DayOut(BaseModel):
    day: str
    runs: int
    failed: int
    cost_usd: Decimal


class KeyedTotalOut(BaseModel):
    key: str | None
    runs: int
    cost_usd: Decimal
    tokens: int


class FleetAnalyticsOut(BaseModel):
    generated_at: datetime
    window_days: int
    agents: list[AgentFleetOut]
    by_day: list[DayOut]
    by_company: list[KeyedTotalOut]
    by_model: list[KeyedTotalOut]
    findings_by_kind: dict[str, int]
    total_cost_usd: Decimal
    total_cost_month_usd: Decimal
    runs_total: int
    runs_month: int
    quality: list[EvalOut]  # latest eval per (phase, sector, company, division)


def _latest_evals(session) -> list[EvalOut]:
    seen: set[tuple] = set()
    out = []
    for r in session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc())):
        scope = (r.phase, r.sector, r.company, r.division)
        if scope not in seen:
            seen.add(scope)
            out.append(EvalOut.model_validate(r))
    return out


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@router.get("/agents/analytics", response_model=FleetAnalyticsOut)
def agents_analytics(principal: Principal = Depends(current_principal)) -> FleetAnalyticsOut:
    now = datetime.now(UTC)
    month = _month_start(now)
    window_start = (now - timedelta(days=WINDOW_DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    with tenant_session(principal.tenant_schema) as session:
        runs_q = select(AgentRun).where(AgentRun.agent_key.in_(AGENT_KEYS))
        if principal.role != "admin":
            runs_q = runs_q.where(visible_deal_clause(AgentRun.deal_id, principal.user_id))
        runs = session.scalars(runs_q.order_by(AgentRun.created_at.desc())).all()
        run_ids: set[uuid.UUID] = {r.id for r in runs}

        findings = (
            session.execute(
                select(Finding.run_id, Finding.kind, Finding.status, Finding.agent_key).where(Finding.run_id.in_(run_ids))
            ).all()
            if run_ids
            else []
        )
        usage = (
            session.execute(
                select(
                    UsageEvent.run_id,
                    UsageEvent.model,
                    UsageEvent.company,
                    UsageEvent.agent_key,
                    UsageEvent.created_at,
                    UsageEvent.input_tokens,
                    UsageEvent.output_tokens,
                    UsageEvent.cost_usd,
                ).where(UsageEvent.run_id.in_(run_ids))
            ).all()
            if run_ids
            else []
        )
        quality = _latest_evals(session)

    per: dict[str, dict] = {
        k: {
            "runs": 0,
            "succeeded": 0,
            "failed": 0,
            "active": 0,
            "last_run_at": None,
            "last_status": None,
            "durations": [],
            "findings_open": 0,
            "findings_total": 0,
            "cost_usd": Decimal(0),
            "cost_month_usd": Decimal(0),
            "tokens": 0,
        }
        for k in AGENT_KEYS
    }
    by_day: dict[str, dict] = defaultdict(lambda: {"runs": 0, "failed": 0, "cost_usd": Decimal(0)})
    runs_month = 0
    for r in runs:
        p = per[r.agent_key]
        p["runs"] += 1
        p["succeeded"] += r.status == "succeeded"
        p["failed"] += r.status == "failed"
        p["active"] += r.status in ("queued", "running", "waiting")
        if p["last_run_at"] is None:
            p["last_run_at"], p["last_status"] = r.created_at, r.status
        if r.started_at and r.finished_at:
            p["durations"].append((r.finished_at - r.started_at).total_seconds())
        if r.created_at >= month:
            runs_month += 1
        if r.created_at >= window_start:
            d = by_day[r.created_at.date().isoformat()]
            d["runs"] += 1
            d["failed"] += r.status == "failed"

    kinds: dict[str, int] = defaultdict(int)
    for _run_id, kind, status, agent_key in findings:
        kinds[kind] += 1
        if agent_key in per:
            per[agent_key]["findings_total"] += 1
            per[agent_key]["findings_open"] += status == "open"

    by_company: dict[str | None, dict] = defaultdict(lambda: {"runs": set(), "cost_usd": Decimal(0), "tokens": 0})
    by_model: dict[str | None, dict] = defaultdict(lambda: {"runs": set(), "cost_usd": Decimal(0), "tokens": 0})
    total = Decimal(0)
    total_month = Decimal(0)
    for run_id, model, company, agent_key, created_at, in_tok, out_tok, cost in usage:
        cost = Decimal(cost or 0)
        tokens = (in_tok or 0) + (out_tok or 0)
        total += cost
        if created_at >= month:
            total_month += cost
        if agent_key in per:
            per[agent_key]["cost_usd"] += cost
            per[agent_key]["tokens"] += tokens
            if created_at >= month:
                per[agent_key]["cost_month_usd"] += cost
        if created_at >= window_start:
            by_day[created_at.date().isoformat()]["cost_usd"] += cost
        for bucket, key in ((by_company, company), (by_model, model)):
            bucket[key]["runs"].add(run_id)
            bucket[key]["cost_usd"] += cost
            bucket[key]["tokens"] += tokens

    days = []
    for i in range(WINDOW_DAYS):
        day = (window_start + timedelta(days=i)).date().isoformat()
        d = by_day.get(day, {"runs": 0, "failed": 0, "cost_usd": Decimal(0)})
        days.append(DayOut(day=day, **d))

    def keyed(bucket: dict) -> list[KeyedTotalOut]:
        rows = [KeyedTotalOut(key=k, runs=len(v["runs"]), cost_usd=v["cost_usd"], tokens=v["tokens"]) for k, v in bucket.items()]
        return sorted(rows, key=lambda x: x.cost_usd, reverse=True)

    return FleetAnalyticsOut(
        generated_at=now,
        window_days=WINDOW_DAYS,
        agents=[
            AgentFleetOut(
                agent_key=k,
                name=AGENT_NAMES[k],
                avg_seconds=(sum(p["durations"]) / len(p["durations"])) if p["durations"] else None,
                **{kk: v for kk, v in p.items() if kk != "durations"},
            )
            for k, p in per.items()
        ],
        by_day=days,
        by_company=keyed(by_company),
        by_model=keyed(by_model),
        findings_by_kind=dict(kinds),
        total_cost_usd=total,
        total_cost_month_usd=total_month,
        runs_total=len(runs),
        runs_month=runs_month,
        quality=quality,
    )
