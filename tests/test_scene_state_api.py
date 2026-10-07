"""set_state end-to-end through the scene command API (#155)."""

from __future__ import annotations

from uuid import uuid4

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import DesignCommandRow, SceneRevisionRow
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


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


async def _init_scene(client: AsyncClient, headers: dict, project_id: str) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/scene",
        headers=headers,
        json={
            "scene_id": "scene.main",
            "project_id": project_id,
            "entities": [
                {"id": "room.living", "kind": "room", "locks": {}},
                {"id": "object.sofa.main", "kind": "furniture", "locks": {}},
            ],
            "cameras": [],
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_set_state_command_creates_revision_and_log(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            created = await client.post(
                "/api/v1/projects", json={"name": "Flat"}, headers=headers
            )
            project_id = created.json()["id"]
            initial = await _init_scene(client, headers, project_id)

            # Scene snapshot carries the default asis layer.
            sofa = next(
                entity
                for entity in initial["scene"]["entities"]
                if entity["id"] == "object.sofa.main"
            )
            assert sofa["state"] == "asis"

            command_id = f"command-{uuid4()}"
            response = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": command_id,
                    "base_revision_id": initial["revision_id"],
                    "operation": "set_state",
                    "target_id": "object.sofa.main",
                    "parameters": {"state": "design"},
                    "origin": "user",
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["parent_revision_id"] == initial["revision_id"]
            sofa = next(
                entity
                for entity in body["scene"]["entities"]
                if entity["id"] == "object.sofa.main"
            )
            assert sofa["state"] == "design"
            # Other entity fields are preserved.
            assert sofa["kind"] == "furniture"

            # Revisions list: the new revision chains onto the base.
            revisions = (await client.get(
                f"/api/v1/projects/{project_id}/scene/revisions"
            )).json()
            assert revisions[0]["revision_id"] == body["revision_id"]
            assert revisions[0]["parent_revision_id"] == initial["revision_id"]
            assert revisions[0]["command_id"] == command_id

            # Audit: the command log row records the operation.
            async with app.state.session_factory() as db:
                row = await db.get(DesignCommandRow, command_id)
                assert row is not None
                assert row.operation == "set_state"
                assert row.target_id == "object.sofa.main"
                assert row.parameters == {"state": "design"}
                assert row.origin == "user"


@pytest.mark.asyncio
async def test_set_state_rejections_use_domain_conflict_style(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            created = await client.post(
                "/api/v1/projects", json={"name": "Flat"}, headers=headers
            )
            project_id = created.json()["id"]
            initial = await _init_scene(client, headers, project_id)

            def command(**overrides):
                payload = {
                    "command_id": f"command-{uuid4()}",
                    "base_revision_id": initial["revision_id"],
                    "operation": "set_state",
                    "target_id": "object.sofa.main",
                    "parameters": {"state": "design"},
                    "origin": "user",
                }
                payload.update(overrides)
                return payload

            unknown_target = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json=command(target_id="object.missing"),
            )
            assert unknown_target.status_code == 409
            assert unknown_target.json()["detail"]["code"] == "command_rejected"

            unknown_state = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json=command(parameters={"state": "demolished"}),
            )
            assert unknown_state.status_code == 409
            assert unknown_state.json()["detail"]["code"] == "command_rejected"

            # Rejections must not have created revisions.
            async with app.state.session_factory() as db:
                rows = (
                    await db.execute(
                        select(DesignCommandRow).where(
                            DesignCommandRow.project_id == project_id
                        )
                    )
                ).scalars()
                assert list(rows) == []


@pytest.mark.asyncio
async def test_add_object_via_api_defaults_to_design(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            created = await client.post(
                "/api/v1/projects", json={"name": "Flat"}, headers=headers
            )
            project_id = created.json()["id"]
            initial = await _init_scene(client, headers, project_id)

            response = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": f"command-{uuid4()}",
                    "base_revision_id": initial["revision_id"],
                    "operation": "add_object",
                    "target_id": "object.chair.accent",
                    "parameters": {
                        "entity": {
                            "id": "object.chair.accent",
                            "kind": "furniture",
                            "display_name": "Accent chair",
                        }
                    },
                    "origin": "user",
                },
            )
            assert response.status_code == 200, response.text
            scene = response.json()["scene"]
            chair = next(
                entity for entity in scene["entities"] if entity["id"] == "object.chair.accent"
            )
            # Old-client compat: no state in the payload lands on design.
            assert chair["state"] == "design"
            # Existing entities keep their layers.
            room = next(entity for entity in scene["entities"] if entity["id"] == "room.living")
            assert room["state"] == "asis"


@pytest.mark.asyncio
async def test_set_state_via_agent_tool_flow(settings):
    """The set_state agent tool converts to a command the API accepts."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            created = await client.post(
                "/api/v1/projects", json={"name": "Flat"}, headers=headers
            )
            project_id = created.json()["id"]
            initial = await _init_scene(client, headers, project_id)

            command_id = f"command-{uuid4()}"
            response = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": command_id,
                    "base_revision_id": initial["revision_id"],
                    "operation": "set_state",
                    "target_id": "room.living",
                    "parameters": {"state": "structure"},
                    "origin": "agent",
                    "request_text": "пометь комнату как конструктив",
                },
            )
            assert response.status_code == 200, response.text
            room = next(
                entity
                for entity in response.json()["scene"]["entities"]
                if entity["id"] == "room.living"
            )
            assert room["state"] == "structure"

            async with app.state.session_factory() as db:
                row = await db.get(DesignCommandRow, command_id)
                assert row.origin == "agent"
                revision = (
                    await db.execute(
                        select(SceneRevisionRow).where(
                            SceneRevisionRow.command_id == command_id
                        )
                    )
                ).scalar_one()
                assert revision.project_id == project_id
