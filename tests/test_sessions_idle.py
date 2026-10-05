from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import AuthSessionRow
from stroy.services.assets import MemoryObjectStore


def idle_settings(tmp_path, **updates) -> Settings:
    values = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'sessions-idle.db'}",
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _login(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response


async def _set_last_seen(app, when: datetime) -> None:
    async with app.state.session_factory() as session:
        result = await session.execute(select(AuthSessionRow))
        result.scalar_one().last_seen_at = when
        await session.commit()


async def _read_last_seen(app) -> datetime | None:
    # Fresh session/query: proves the dependency's write was persisted.
    async with app.state.session_factory() as session:
        result = await session.execute(select(AuthSessionRow))
        return result.scalar_one().last_seen_at


@pytest.mark.asyncio
async def test_authenticated_request_records_last_seen(tmp_path):
    app = create_app(settings=idle_settings(tmp_path), object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _login(client)
            # Login does not set last_seen_at; the first authenticated request does.
            assert await _read_last_seen(app) is None

            assert (await client.get("/api/v1/projects")).status_code == 200
            recorded = await _read_last_seen(app)
            assert recorded is not None
            assert abs(
                (datetime.now(timezone.utc) - _as_utc(recorded)).total_seconds()
            ) < 60


@pytest.mark.asyncio
async def test_idle_session_past_ttl_is_rejected(tmp_path):
    app = create_app(
        settings=idle_settings(tmp_path, session_idle_ttl_seconds=3600),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _login(client)
            await _set_last_seen(
                app, datetime.now(timezone.utc) - timedelta(seconds=7200)
            )

            assert (await client.get("/api/v1/projects")).status_code == 401


@pytest.mark.asyncio
async def test_activity_extends_idle_window(tmp_path):
    app = create_app(
        settings=idle_settings(tmp_path, session_idle_ttl_seconds=3600),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _login(client)
            backdated = datetime.now(timezone.utc) - timedelta(seconds=1800)
            await _set_last_seen(app, backdated)

            assert (await client.get("/api/v1/projects")).status_code == 200
            extended = await _read_last_seen(app)
            assert extended is not None
            assert _as_utc(extended) > backdated


@pytest.mark.asyncio
async def test_idle_expiry_disabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STROY_SESSION_IDLE_TTL", "0")
    assert Settings(_env_file=None).session_idle_ttl_seconds == 0

    app = create_app(settings=idle_settings(tmp_path), object_store=MemoryObjectStore())
    assert app.state.settings.session_idle_ttl_seconds == 0
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _login(client)
            # Way beyond any idle window: still valid because idle is disabled.
            await _set_last_seen(
                app, datetime.now(timezone.utc) - timedelta(days=365)
            )
            assert (await client.get("/api/v1/projects")).status_code == 200


def test_negative_idle_ttl_rejected():
    # A typo like STROY_SESSION_IDLE_TTL=-1 must fail loudly instead of
    # silently disabling idle expiry (validator finding on PR #132).
    with pytest.raises(ValidationError):
        Settings(_env_file=None, session_idle_ttl_seconds=-1)


@pytest.mark.asyncio
async def test_absolute_expiry_still_enforced_when_active(tmp_path):
    app = create_app(
        settings=idle_settings(tmp_path, session_idle_ttl_seconds=3600),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _login(client)

            async with app.state.session_factory() as session:
                result = await session.execute(select(AuthSessionRow))
                row = result.scalar_one()
                row.last_seen_at = datetime.now(timezone.utc)
                row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()

            assert (await client.get("/api/v1/projects")).status_code == 401


@pytest.mark.asyncio
async def test_revoked_session_still_rejected(tmp_path):
    app = create_app(
        settings=idle_settings(tmp_path, session_idle_ttl_seconds=3600),
        object_store=MemoryObjectStore(),
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _login(client)

            async with app.state.session_factory() as session:
                result = await session.execute(select(AuthSessionRow))
                row = result.scalar_one()
                row.last_seen_at = datetime.now(timezone.utc)
                row.revoked_at = datetime.now(timezone.utc)
                await session.commit()

            assert (await client.get("/api/v1/projects")).status_code == 401
