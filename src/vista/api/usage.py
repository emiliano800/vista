from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func, select

from vista.api.schemas import UsageOut
from vista.auth import Principal, current_principal
from vista.db import tenant_session
from vista.models.tenant import UsageEvent

router = APIRouter(tags=["usage"])


@router.get("/usage", response_model=UsageOut)
def get_usage(principal: Principal = Depends(current_principal)) -> UsageOut:
    with tenant_session(principal.tenant_schema) as session:
        row = session.execute(
            select(
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0),
                func.count(distinct(UsageEvent.run_id)),
            )
        ).one()
        return UsageOut(
            total_input_tokens=row[0],
            total_output_tokens=row[1],
            total_cost_usd=Decimal(row[2]),
            runs=row[3],
        )
