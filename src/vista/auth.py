import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from vista.db import platform_session
from vista.models.platform import Tenant, User

bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_schema: str
    email: str
    role: str


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    with platform_session() as session:
        row = session.execute(
            select(User, Tenant.schema_name)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.api_token == credentials.credentials)
        ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid token")
    user, schema = row
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        tenant_schema=schema,
        email=user.email,
        role=user.role,
    )


def admin_principal(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
