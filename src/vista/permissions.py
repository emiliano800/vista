import uuid

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from vista.auth import Principal
from vista.models.tenant import DealMembership

ROLE_RANK = {"viewer": 0, "member": 1, "owner": 2}


def require_deal_role(session: Session, deal_id: uuid.UUID, user_id: uuid.UUID, minimum: str) -> str:
    """Raise 403 unless the user has at least `minimum` role on the deal.
    Session must already be scoped to the caller's tenant schema."""
    role = session.scalar(select(DealMembership.role).where(DealMembership.deal_id == deal_id, DealMembership.user_id == user_id))
    if role is None or ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="insufficient permissions on deal")
    return role


def visible_deal_clause(deal_column, user_id: uuid.UUID):
    """Filter for list endpoints: rows without a deal (portfolio-wide runs such as the
    Sector Merger) plus rows on deals the user belongs to. Admins skip this."""
    member_of = select(DealMembership.deal_id).where(DealMembership.user_id == user_id)
    return or_(deal_column.is_(None), deal_column.in_(member_of))


def require_agent_role(session: Session, principal: Principal, deal_id: uuid.UUID | None, minimum: str = "owner") -> None:
    """Who may start an agent run. Admins always can. Otherwise a run scoped to a
    deal needs `minimum` on that deal; a portfolio-wide run (Sector Merger, company
    summary) needs `minimum` on at least one deal, because its findings span deals
    the caller can already see."""
    if principal.role == "admin":
        return
    if deal_id is not None:
        require_deal_role(session, deal_id, principal.user_id, minimum)
        return
    roles = session.scalars(select(DealMembership.role).where(DealMembership.user_id == principal.user_id)).all()
    if not any(ROLE_RANK[r] >= ROLE_RANK[minimum] for r in roles):
        raise HTTPException(status_code=403, detail=f"{minimum} role on a deal required to start agent runs")
