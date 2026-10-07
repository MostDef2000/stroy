"""Validation API (#R2): run/latest endpoints, persistence, read-only contract."""

from __future__ import annotations

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import event, func, select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import ValidationReportRow
from stroy.services.assets import MemoryObjectStore


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        auto_create_schema=True,
        auth_username="owner",
        auth_password_hash=PasswordHasher().hash("secret"),
        session_cookie_secure=False,
        trusted_hosts="test",
        worker_token="worker-secret",
        storage_backend="memory",
    )


def _enable_sqlite_foreign_keys(app) -> None:
    engine = app.state.session_factory.kw["bind"]

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


async def _create_project(client: AsyncClient, headers: dict, name: str = "Flat") -> str:
    response = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def _init_scene(client: AsyncClient, headers: dict, project_id: str) -> str:
    """Room + two overlapping sofas; returns the base revision id."""
    response = await client.post(
        f"/api/v1/projects/{project_id}/scene",
        headers=headers,
        json={
            "scene_id": "scene.main",
            "project_id": project_id,
            "entities": [
                {
                    "id": "room.living",
                    "kind": "room",
                    "room_id": "room.living",
                    "geometry": {
                        "dimensions_mm": [5000, 4000, 2800],
                        "bbox": [0, 0, 5000, 4000],
                    },
                    "locks": {},
                },
                {
                    "id": "floor.room.living",
                    "kind": "floor",
                    "room_id": "room.living",
                    "geometry": {
                        "dimensions_mm": [5000, 4000, 0],
                        "bbox": [0, 0, 5000, 4000],
                    },
                    "locks": {},
                },
                {
                    "id": "object.sofa.a",
                    "kind": "furniture",
                    "room_id": "room.living",
                    "transform": {"translation_mm": [1000, 2000, 0]},
                    "geometry": {"dimensions_mm": [2000, 900, 800]},
                    "locks": {},
                },
                {
                    "id": "object.sofa.b",
                    "kind": "furniture",
                    "room_id": "room.living",
                    "transform": {"translation_mm": [1400, 2000, 0]},
                    "geometry": {"dimensions_mm": [2000, 900, 800]},
                    "locks": {},
                },
            ],
            "cameras": [],
        },
    )
    assert response.status_code == 201
    return response.json()["revision_id"]


async def _advance_revision(
    client: AsyncClient, headers: dict, project_id: str, base_revision_id: str
) -> str:
    """A harmless color command -> a second revision."""
    response = await client.post(
        f"/api/v1/projects/{project_id}/scene/commands",
        headers=headers,
        json={
            "command_id": f"command-{base_revision_id}",
            "base_revision_id": base_revision_id,
            "operation": "set_color",
            "target_id": "object.sofa.a",
            "parameters": {"color": "#D7C4AB"},
            "origin": "user",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["revision_id"]


@pytest.mark.asyncio
async def test_validation_run_on_latest_revision_persists_report(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    _enable_sqlite_foreign_keys(app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id = await _init_scene(client, headers, project_id)

            created = await client.post(
                f"/api/v1/projects/{project_id}/validation",
                headers=headers,
                json={},
            )
            assert created.status_code == 200, created.text
            body = created.json()
            assert body["project_id"] == project_id
            assert body["scene_revision_id"] == revision_id
            assert body["id"] and body["created_at"]
            assert body["scene_content_hash"] and body["config_hash"] and body["report_hash"]
            report = body["report"]
            assert report["scene_revision_id"] == revision_id
            assert report["summary"]["error"] >= 1  # the two sofas overlap
            assert any(
                item["rule_id"] == "object.object_collision" for item in report["results"]
            )

            # Exactly one persisted row with the same payload.
            async with app.state.session_factory() as session:
                rows = (await session.execute(select(ValidationReportRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].id == body["id"]
            assert rows[0].scene_revision_id == revision_id


@pytest.mark.asyncio
async def test_validation_explicit_revision_and_latest_default(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            base_revision = await _init_scene(client, headers, project_id)
            second_revision = await _advance_revision(
                client, headers, project_id, base_revision
            )

            explicit = await client.post(
                f"/api/v1/projects/{project_id}/validation",
                headers=headers,
                json={"scene_revision_id": base_revision},
            )
            assert explicit.status_code == 200
            assert explicit.json()["scene_revision_id"] == base_revision

            latest = await client.post(
                f"/api/v1/projects/{project_id}/validation", headers=headers, json={}
            )
            assert latest.status_code == 200
            assert latest.json()["scene_revision_id"] == second_revision
            # A different config would change config_hash; same defaults do not.
            assert latest.json()["config_hash"] == explicit.json()["config_hash"]
            assert latest.json()["id"] != explicit.json()["id"]


@pytest.mark.asyncio
async def test_validation_latest_endpoint_404_then_200(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id = await _init_scene(client, headers, project_id)

            # No report yet -> 404.
            missing = await client.get(
                f"/api/v1/projects/{project_id}/validation/latest"
            )
            assert missing.status_code == 404

            default_run = await client.post(
                f"/api/v1/projects/{project_id}/validation", headers=headers, json={}
            )
            assert default_run.status_code == 200

            # A second run with a custom config (different config_hash).
            custom_run = await client.post(
                f"/api/v1/projects/{project_id}/validation",
                headers=headers,
                json={"min_walkway_mm": 900},
            )
            assert custom_run.status_code == 200
            assert custom_run.json()["id"] != default_run.json()["id"]

            # GET latest without params returns the DEFAULT-config report.
            fetched = await client.get(f"/api/v1/projects/{project_id}/validation/latest")
            assert fetched.status_code == 200
            assert fetched.json()["id"] == default_run.json()["id"]
            assert fetched.json()["report"]["config"]["min_walkway_mm"] == 600.0

            # The endpoint can be pinned to an explicit revision.
            pinned = await client.get(
                f"/api/v1/projects/{project_id}/validation/latest",
                params={"scene_revision_id": revision_id},
            )
            assert pinned.status_code == 200
            assert pinned.json()["id"] == default_run.json()["id"]


@pytest.mark.asyncio
async def test_validation_rejects_invalid_config(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            for bad in (0, -100):
                response = await client.post(
                    f"/api/v1/projects/{project_id}/validation",
                    headers=headers,
                    json={"min_walkway_mm": bad},
                )
                assert response.status_code == 422
                assert response.json()["detail"]["code"] == "invalid_validation_config"

            async with app.state.session_factory() as session:
                count = (
                    await session.execute(select(func.count()).select_from(ValidationReportRow))
                ).scalar_one()
            assert count == 0


@pytest.mark.asyncio
async def test_validation_unknown_revision_404(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            response = await client.post(
                f"/api/v1/projects/{project_id}/validation",
                headers=headers,
                json={"scene_revision_id": "rev-does-not-exist"},
            )
            assert response.status_code == 404


@pytest.mark.asyncio
async def test_validation_does_not_create_scene_revisions(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            before = (
                await client.get(f"/api/v1/projects/{project_id}/scene/revisions")
            ).json()
            for _ in range(2):
                run = await client.post(
                    f"/api/v1/projects/{project_id}/validation",
                    headers=headers,
                    json={"min_walkway_mm": 750},
                )
                assert run.status_code == 200
            after = (
                await client.get(f"/api/v1/projects/{project_id}/scene/revisions")
            ).json()

            assert [row["revision_id"] for row in before] == [
                row["revision_id"] for row in after
            ]
            assert len(after) == 1
