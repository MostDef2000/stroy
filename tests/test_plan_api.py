from __future__ import annotations

from collections import Counter
from io import BytesIO

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from stroy.api.app import create_app
from stroy.config import Settings
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


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 3), "white").save(buffer, format="PNG")
    return buffer.getvalue()


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def wall(
    wall_id: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    openings: list[dict] | None = None,
) -> dict:
    return {
        "id": wall_id,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "thickness_mm": 150.0,
        "openings": openings or [],
    }


def two_room_draft(*, scale: dict | None = None) -> dict:
    return {
        "version": "0.1.0",
        "units": "mm",
        "scale": scale or {"source": "manual", "mm_per_px": 1.0},
        "floors": [
            {
                "name": "main",
                "level_mm": 0.0,
                "walls": [
                    wall("wall.1", 0, 0, 4000, 0),
                    wall("wall.2", 4000, 0, 4000, 3000),
                    wall(
                        "wall.3",
                        4000,
                        3000,
                        0,
                        3000,
                        openings=[
                            {
                                "id": "opening.window.1",
                                "kind": "window",
                                "t": 0.5,
                                "width_mm": 1600,
                                "height_mm": 1200,
                                "sill_mm": 900,
                            }
                        ],
                    ),
                    wall("wall.4", 0, 3000, 0, 0),
                    wall("wall.5", 5000, 0, 8000, 0),
                    wall("wall.6", 8000, 0, 8000, 3000),
                    wall("wall.7", 8000, 3000, 5000, 3000),
                    wall(
                        "wall.8",
                        5000,
                        3000,
                        5000,
                        0,
                        openings=[
                            {
                                "id": "opening.door.1",
                                "kind": "door",
                                "t": 0.25,
                                "width_mm": 900,
                                "height_mm": 2100,
                                "sill_mm": 0,
                            }
                        ],
                    ),
                ],
                "rooms": [
                    {
                        "id": "room.living",
                        "name": "Living room",
                        "wall_ids": ["wall.1", "wall.2", "wall.3", "wall.4"],
                        "floor_finish": None,
                    },
                    {
                        "id": "room.bedroom",
                        "name": "Bedroom",
                        "wall_ids": ["wall.5", "wall.6", "wall.7", "wall.8"],
                        "floor_finish": None,
                    },
                ],
            }
        ],
    }


async def _create_project(client: AsyncClient, headers: dict) -> str:
    response = await client.post(
        "/api/v1/projects", json={"name": "Plan project"}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _upload_plan_image(
    client: AsyncClient, project_id: str, headers: dict
) -> str:
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        data={"role": "reference"},
        files={"file": ("plan.png", png_bytes(), "image/png")},
    )
    assert upload.status_code == 201
    return upload.json()["id"]


@pytest.mark.asyncio
async def test_plan_analyze_queues_plan_job(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            asset_id = await _upload_plan_image(client, project_id, headers)

            analyze = await client.post(
                f"/api/v1/projects/{project_id}/plan/analyze",
                headers=headers,
                json={
                    "asset_ids": [asset_id],
                    "hints": {
                        "known_wall_length_mm": 5000,
                        "wall_asset_index": 0,
                        "length_mm": 1000,
                    },
                },
            )
            assert analyze.status_code == 201
            body = analyze.json()
            assert body["job_type"] == "plan.analyze"
            assert body["job_id"]
            assert body["draft_id"] is None

            worker_headers = {"Authorization": "Bearer worker-secret"}
            await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "plan-worker",
                    "capabilities": ["plan_analyze"],
                    "models": ["fake"],
                },
            )
            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "plan-worker"},
            )
            assert claim.status_code == 200
            lease = claim.json()
            assert lease["job_type"] == "plan.analyze"
            assert lease["input_asset_ids"] == [asset_id]
            assert lease["required_capabilities"] == ["plan_analyze"]
            assert lease["payload"]["purpose"] == "plan_draft"
            assert lease["payload"]["hints"]["known_wall_length_mm"] == 5000


@pytest.mark.asyncio
async def test_plan_analyze_rejects_non_image_asset(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/plan/analyze",
                headers=headers,
                json={"asset_ids": ["missing-asset"]},
            )
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "invalid_plan_asset"


@pytest.mark.asyncio
async def test_plan_draft_save_and_get_latest(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            first = await client.put(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
                json={"draft": two_room_draft()},
            )
            assert first.status_code == 200
            assert first.json()["version"] == 1
            assert first.json()["status"] == "draft"

            second = await client.put(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
                json={"draft": two_room_draft()},
            )
            assert second.status_code == 200
            assert second.json()["version"] == 2

            fetched = await client.get(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
            )
            assert fetched.status_code == 200
            body = fetched.json()
            assert body["version"] == 2
            assert body["draft"]["floors"][0]["rooms"][0]["id"] == "room.living"


@pytest.mark.asyncio
async def test_plan_draft_commit_creates_scene_revision(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            saved = await client.put(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
                json={"draft": two_room_draft()},
            )
            assert saved.status_code == 200

            commit = await client.post(
                f"/api/v1/projects/{project_id}/plan/draft/commit",
                headers=headers,
            )
            assert commit.status_code == 200
            revision_id = commit.json()["revision_id"]
            assert revision_id

            scene = await client.get(
                f"/api/v1/projects/{project_id}/scene", headers=headers
            )
            assert scene.status_code == 200
            entities = scene.json()["scene"]["entities"]
            counts = Counter(entity["kind"] for entity in entities)
            assert counts["wall"] == 8
            assert counts["door"] == 1
            assert counts["window"] == 1
            assert counts["room"] == 2
            assert counts["floor"] == 2
            assert counts["ceiling"] == 2

            fetched = await client.get(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
            )
            assert fetched.json()["status"] == "committed"


@pytest.mark.asyncio
async def test_plan_draft_commit_unknown_scale_returns_422(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            saved = await client.put(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
                json={
                    "draft": two_room_draft(
                        scale={"source": "unknown", "mm_per_px": None}
                    )
                },
            )
            assert saved.status_code == 200

            commit = await client.post(
                f"/api/v1/projects/{project_id}/plan/draft/commit",
                headers=headers,
            )
            assert commit.status_code == 422
            assert commit.json()["detail"]["code"] == "scale_unknown"

            missing_scene = await client.get(
                f"/api/v1/projects/{project_id}/scene", headers=headers
            )
            assert missing_scene.status_code == 404


@pytest.mark.asyncio
async def test_plan_draft_double_commit_returns_409(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            saved = await client.put(
                f"/api/v1/projects/{project_id}/plan/draft",
                headers=headers,
                json={"draft": two_room_draft()},
            )
            assert saved.status_code == 200

            first = await client.post(
                f"/api/v1/projects/{project_id}/plan/draft/commit",
                headers=headers,
            )
            assert first.status_code == 200

            second = await client.post(
                f"/api/v1/projects/{project_id}/plan/draft/commit",
                headers=headers,
            )
            assert second.status_code == 409
            assert second.json()["detail"]["code"] == "draft_already_committed"

            revisions = await client.get(
                f"/api/v1/projects/{project_id}/scene/revisions",
                headers=headers,
            )
            assert revisions.status_code == 200
            assert len(revisions.json()) == 1
