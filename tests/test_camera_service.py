from __future__ import annotations

import json
from pathlib import Path

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
import pytest

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.domain.models import Camera, CameraCalibration, CameraTransform, Scene
from stroy.services.assets import MemoryObjectStore
from stroy.services.cameras import upsert_camera
from stroy.services.scenes import create_project, initialize_scene


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_TRANSLATION = (0.0, -5000.0, 1500.0)
GOLDEN_ROTATION = (90.0, 0.0, 0.0)


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


def _golden_camera() -> Camera:
    data = json.loads(
        (ROOT / "fixtures" / "golden-camera.correspondences.json").read_text(
            encoding="utf-8"
        )
    )
    return Camera.model_validate(data["camera"])


async def _seed_scene(session) -> tuple[str, str]:
    project = await create_project(session, "Camera project")
    scene = Scene(
        scene_id="scene.camera",
        project_id=project.id,
        entities=[],
        cameras=[],
    )
    revision = await initialize_scene(session, project.id, scene)
    return project.id, revision.id


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.mark.asyncio
async def test_upsert_camera_solve_fills_pose_and_calibration(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            project_id, base_revision_id = await _seed_scene(session)
            camera = _golden_camera()
            # An all-zero transform is the "not solved yet" pose a client sends.
            camera.transform = CameraTransform()

            revision = await upsert_camera(
                session,
                project_id,
                expected_base_revision_id=base_revision_id,
                camera=camera,
                solve=True,
            )

            stored = revision.scene_json["cameras"][0]
            assert stored["transform"]["translation_mm"] == pytest.approx(
                GOLDEN_TRANSLATION, abs=1e-6
            )
            assert stored["transform"]["rotation_deg"] == pytest.approx(
                GOLDEN_ROTATION, abs=1e-6
            )
            calibration = stored["calibration"]
            assert calibration["method"] == "correspondences"
            assert calibration["residual"] == pytest.approx(0.0, abs=1e-6)
            assert calibration["quality"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_upsert_camera_without_solve_keeps_explicit_transform(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            project_id, base_revision_id = await _seed_scene(session)
            camera = _golden_camera()
            # Deliberately wrong but still in front of the camera: validate-only
            # must preserve it and report the resulting residual.
            camera.transform = CameraTransform(
                translation_mm=(0.0, -4500.0, 1500.0),
                rotation_deg=(90.0, 0.0, 0.0),
            )

            revision = await upsert_camera(
                session,
                project_id,
                expected_base_revision_id=base_revision_id,
                camera=camera,
                solve=False,
            )

            stored = revision.scene_json["cameras"][0]
            assert stored["transform"]["translation_mm"] == pytest.approx(
                [0.0, -4500.0, 1500.0], abs=1e-9
            )
            assert stored["transform"]["rotation_deg"] == pytest.approx(
                [90.0, 0.0, 0.0], abs=1e-9
            )
            calibration = stored["calibration"]
            assert calibration["method"] == "correspondences"
            assert calibration["residual"] > 0.5


@pytest.mark.asyncio
async def test_upsert_camera_solve_requires_observations(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            project_id, base_revision_id = await _seed_scene(session)
            camera = _golden_camera()
            camera.calibration = None

            with pytest.raises(ValueError, match="requires calibration observations"):
                await upsert_camera(
                    session,
                    project_id,
                    expected_base_revision_id=base_revision_id,
                    camera=camera,
                    solve=True,
                )


@pytest.mark.asyncio
async def test_upsert_camera_solve_requires_observations_when_empty(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            project_id, base_revision_id = await _seed_scene(session)
            camera = _golden_camera()
            camera.calibration = CameraCalibration(method="manual", observations=[])

            with pytest.raises(ValueError, match="requires calibration observations"):
                await upsert_camera(
                    session,
                    project_id,
                    expected_base_revision_id=base_revision_id,
                    camera=camera,
                    solve=True,
                )


@pytest.mark.asyncio
async def test_camera_upsert_api_passes_solve_flag(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            project = await client.post(
                "/api/v1/projects",
                headers=headers,
                json={"name": "Camera solve project"},
            )
            project_id = project.json()["id"]

            initial = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.camera",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = initial.json()["revision_id"]

            camera = _golden_camera().model_dump(mode="json", exclude_none=True)
            camera["transform"] = {
                "translation_mm": [0, 0, 0],
                "rotation_deg": [0, 0, 0],
            }

            update = await client.put(
                f"/api/v1/projects/{project_id}/cameras/{camera['id']}",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "camera": camera,
                    "solve": True,
                },
            )

            assert update.status_code == 200
            stored = update.json()["camera"]
            assert stored["transform"]["translation_mm"] == pytest.approx(
                list(GOLDEN_TRANSLATION), abs=1e-6
            )
            assert stored["transform"]["rotation_deg"] == pytest.approx(
                list(GOLDEN_ROTATION), abs=1e-6
            )
            assert stored["calibration"]["residual"] == pytest.approx(0.0, abs=1e-6)
