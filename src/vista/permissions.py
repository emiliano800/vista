import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from vista.models.tenant import DealMembership

ROLE_RANK = {"viewer": 0, "member": 1, "owner": 2}


def require_deal_role(session: Session, deal_id: uuid.UUID, user_id: uuid.UUID, minimum: str) -> str:
    """Raise 403 unless the user has at least `minimum` role on the deal.
    Session must already be scoped to the caller's tenant schema."""
    role = session.scalar(
        select(DealMembership.role).where(
            DealMembership.deal_id == deal_id, DealMembership.user_id == user_id
        )
    )
    if role is None or ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="insufficient permissions on deal")
    return role
