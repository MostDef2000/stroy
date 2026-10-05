from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
import pytest

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.services.assets import MemoryObjectStore


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


@pytest.fixture(autouse=True)
def _clear_stroy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("STROY_"):
            monkeypatch.delenv(name, raising=False)


def _base_kwargs(tmp_path: Path) -> dict[str, Any]:
    return {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "auto_create_schema": True,
        "trusted_hosts": "test",
        "storage_backend": "memory",
    }


def _production_credentials() -> dict[str, str]:
    return {
        "session_secret": _runtime_value("session"),
        "worker_token": _runtime_value("worker"),
    }


def _development_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, **_base_kwargs(tmp_path))


def _production_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        env="production",
        **_base_kwargs(tmp_path),
        **_production_credentials(),
    )


@asynccontextmanager
async def _running_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_production_disables_docs_and_schema_endpoints(tmp_path: Path) -> None:
    async with _running_client(_production_settings(tmp_path)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = await client.get(path)
            assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_development_serves_docs_and_schema_endpoints(tmp_path: Path) -> None:
    async with _running_client(_development_settings(tmp_path)) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = await client.get(path)
            assert response.status_code == 200, path


@pytest.mark.asyncio
async def test_production_still_serves_health(tmp_path: Path) -> None:
    async with _running_client(_production_settings(tmp_path)) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
