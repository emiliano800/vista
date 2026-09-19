import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from vista.config import settings
from vista.db import platform_session
from vista.models.platform import BrowserSession, Tenant, User
from vista.security import token_digest

bearer = HTTPBearer(auto_error=False)
COOKIE = "vista_session"


@dataclass
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_schema: str
    email: str
    role: str


def principal_for_key(token: str) -> Principal:
    if not 32 <= len(token) <= 256:
        raise HTTPException(401, "Invalid access key")
    with platform_session() as session:
        row = session.execute(
            select(User, Tenant.schema_name).join(Tenant, Tenant.id == User.tenant_id).where(User.api_token_hash == token_digest(token))
        ).first()
    return _principal(row)


def _principal(row) -> Principal:
    if row is None:
        raise HTTPException(401, "Invalid or expired credentials")
    user, schema = row
    return Principal(user.id, user.tenant_id, schema, user.email, user.role)


def check_browser_request(request: Request):
    # Custom header forces cross-origin callers through a CORS preflight.
    if request.headers.get("x-vista-request") != "1":
        raise HTTPException(403, "Missing browser request header")
    origin = request.headers.get("origin")
    same_origin = str(request.base_url).rstrip("/")
    if origin and origin not in [same_origin, *settings.allowed_origins]:
        raise HTTPException(403, "Origin not allowed")


def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    if credentials is not None:
        return principal_for_key(credentials.credentials)
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        raise HTTPException(401, "Sign in to continue")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_browser_request(request)
    with platform_session() as session:
        row = session.execute(
            select(User, Tenant.schema_name)
            .join(Tenant, Tenant.id == User.tenant_id)
            .join(BrowserSession, BrowserSession.user_id == User.id)
            .where(
                BrowserSession.token_hash == token_digest(cookie),
                BrowserSession.expires_at > datetime.now(UTC),
                BrowserSession.key_hash == User.api_token_hash,
            )
        ).first()
    return _principal(row)


def admin_principal(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(403, "admin role required")
    return principal
