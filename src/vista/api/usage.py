import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func, select

from vista.api.schemas import UsageGroupOut, UsageOut
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import AgentRun, UsageEvent
from vista.permissions import require_deal_role, visible_deal_clause

router = APIRouter(tags=["usage"])

GROUP_COLUMNS = {
    "company": UsageEvent.company,
    "division": AgentRun.division,
    "sector": AgentRun.sector,
    "model": UsageEvent.model,
    "agent_key": UsageEvent.agent_key,
    "run_type": AgentRun.run_type,
}

_TOTALS = (
    func.coalesce(func.sum(UsageEvent.input_tokens), 0),
    func.coalesce(func.sum(UsageEvent.output_tokens), 0),
    func.coalesce(func.sum(UsageEvent.cost_usd), 0),
    func.count(distinct(UsageEvent.run_id)),
)


@router.get("/usage", response_model=UsageOut)
def get_usage(
    group_by: list[str] = Query(default=[]),
    since: datetime | None = None,
    company: str | None = None,
    agent_key: str | None = None,
    deal_id: uuid.UUID | None = None,
    principal: Principal = Depends(current_principal),
) -> UsageOut:
    """Token and cost totals, optionally broken down by company/division/sector/model/agent_key/run_type.
    Scoped like /runs for non-admins."""
    if unknown := sorted(set(group_by) - set(GROUP_COLUMNS)):
        raise HTTPException(status_code=422, detail=f"group_by must be among {sorted(GROUP_COLUMNS)}, got {unknown}")
    base = select(*_TOTALS).join(AgentRun, AgentRun.id == UsageEvent.run_id)
    if since is not None:
        base = base.where(UsageEvent.created_at >= since)
    if company is not None:
        base = base.where(UsageEvent.company == company)
    if agent_key is not None:
        base = base.where(UsageEvent.agent_key == agent_key)
    if deal_id is not None:
        base = base.where(AgentRun.deal_id == deal_id)
    with tenant_session(principal.tenant_schema) as session:
        if deal_id is not None and principal.role != "admin":
            require_deal_role(session, deal_id, principal.user_id, "viewer")
        elif principal.role != "admin":
            base = base.where(visible_deal_clause(AgentRun.deal_id, principal.user_id))
        row = session.execute(base).one()
        groups: list[UsageGroupOut] = []
        if group_by:
            columns = [GROUP_COLUMNS[g] for g in group_by]
            grouped = base.add_columns(*columns).group_by(*columns).order_by(_TOTALS[2].desc())
            for g in session.execute(grouped).all():
                groups.append(
                    UsageGroupOut(
                        key=dict(zip(group_by, g[4:], strict=True)),
                        input_tokens=g[0],
                        output_tokens=g[1],
                        cost_usd=Decimal(g[2]),
                        runs=g[3],
                    )
                )
        return UsageOut(
            total_input_tokens=row[0],
            total_output_tokens=row[1],
            total_cost_usd=Decimal(row[2]),
            runs=row[3],
            groups=groups,
        )
