import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete

from vista.auth import COOKIE, Principal, check_browser_request, current_principal, principal_for_key
from vista.config import settings
from vista.db import platform_session
from vista.models.platform import BrowserSession
from vista.security import token_digest

router = APIRouter(prefix="/auth", tags=["sessions"])


class Login(BaseModel):
    token: str = Field(min_length=32, max_length=256, repr=False)


@router.post("/session")
def login(body: Login, request: Request, response: Response):
    check_browser_request(request)
    principal = principal_for_key(body.token)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with platform_session() as session:
        session.execute(delete(BrowserSession).where(BrowserSession.expires_at <= now))
        old = request.cookies.get(COOKIE)
        if old:
            session.execute(delete(BrowserSession).where(BrowserSession.token_hash == token_digest(old)))
        session.add(
            BrowserSession(
                token_hash=token_digest(token),
                user_id=principal.user_id,
                key_hash=token_digest(body.token),
                expires_at=now + timedelta(hours=settings.session_hours),
            )
        )
        session.commit()
    response.set_cookie(
        COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_hours * 3600, path="/"
    )
    response.headers["Cache-Control"] = "no-store"
    return {"email": principal.email, "tenant_id": str(principal.tenant_id)}


@router.get("/me")
def me(principal: Principal = Depends(current_principal)):
    return {"email": principal.email, "tenant_id": str(principal.tenant_id), "user_id": str(principal.user_id)}


@router.delete("/session", status_code=204)
def logout(request: Request, response: Response):
    check_browser_request(request)
    token = request.cookies.get(COOKIE)
    if token:
        with platform_session() as session:
            session.execute(delete(BrowserSession).where(BrowserSession.token_hash == token_digest(token)))
            session.commit()
    response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
