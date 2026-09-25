from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import AuthSessionRow
from stroy.services.assets import MemoryObjectStore


def auth_settings(tmp_path, **updates) -> Settings:
    values = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}",
        "auto_create_schema": True,
        "auth_username": "owner",
        "auth_password_hash": PasswordHasher().hash("secret"),
        "session_cookie_secure": False,
        "trusted_hosts": "test",
        "worker_token": "worker-secret",
        "storage_backend": "memory",
    }
    values.update(updates)
    return Settings(**values)


@pytest.mark.asyncio
async def test_invalid_password_is_rejected(tmp_path):
    app = create_app(
        settings=auth_settings(tmp_path),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "wrong"},
            )
            assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_owner_session(tmp_path):
    app = create_app(
        settings=auth_settings(tmp_path),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "secret"},
            )
            csrf = login.json()["csrf_token"]
            assert (await client.get("/api/v1/projects")).status_code == 200

            logout = await client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": csrf},
            )
            assert logout.status_code == 200
            assert (await client.get("/api/v1/projects")).status_code == 401


@pytest.mark.asyncio
async def test_expired_session_is_rejected(tmp_path):
    app = create_app(
        settings=auth_settings(tmp_path),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "secret"},
            )
            assert login.status_code == 200

            async with app.state.session_factory() as session:
                result = await session.execute(select(AuthSessionRow))
                row = result.scalar_one()
                row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()

            assert (await client.get("/api/v1/projects")).status_code == 401


@pytest.mark.asyncio
async def test_worker_bearer_is_not_owner_auth(tmp_path):
    app = create_app(
        settings=auth_settings(tmp_path),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/projects",
                headers={"Authorization": "Bearer worker-secret"},
            )
            assert response.status_code == 401
