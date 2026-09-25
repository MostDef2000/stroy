from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.config import Settings
from stroy.db.models import AuthSessionRow
from stroy.security import sha256_text, verify_worker_token


async def get_db(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


def settings_from(request: Request) -> Settings:
    return request.app.state.settings


async def require_owner(request: Request, session: DbSession) -> AuthSessionRow:
    token = request.cookies.get("stroy_session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required"
        )
    result = await session.execute(
        select(AuthSessionRow).where(
            AuthSessionRow.token_hash == sha256_text(token),
            AuthSessionRow.revoked_at.is_(None),
        )
    )
    auth_session = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if auth_session is None or auth_session.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    request.state.auth_session = auth_session
    return auth_session


OwnerSession = Annotated[AuthSessionRow, Depends(require_owner)]


async def require_csrf(
    owner: OwnerSession,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    if not x_csrf_token or x_csrf_token != owner.csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")


async def require_worker(request: Request) -> None:
    settings = settings_from(request)
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="worker auth required"
        )
    token = value.removeprefix("Bearer ").strip()
    if not verify_worker_token(token, settings.worker_token, settings.worker_token_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid worker token")
