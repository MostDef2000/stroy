"""R6 guided-setup endpoint (#183): GET /api/v1/projects/{project_id}/setup."""

from __future__ import annotations

from io import BytesIO

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import SceneRevisionRow
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


def wall(wall_id: str, x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "id": wall_id,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "thickness_mm": 150.0,
        "openings": [],
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
                    wall("wall.3", 4000, 3000, 0, 3000),
                    wall("wall.4", 0, 3000, 0, 0),
                    wall("wall.5", 5000, 0, 8000, 0),
                    wall("wall.6", 8000, 0, 8000, 3000),
                    wall("wall.7", 8000, 3000, 5000, 3000),
                    wall("wall.8", 5000, 3000, 5000, 0),
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


def renamed_rooms_draft() -> dict:
    """Same topology as two_room_draft, different room ids and names."""
    draft = two_room_draft()
    draft["floors"][0]["rooms"] = [
        {
            "id": "room.kitchen",
            "name": "Kitchen",
            "wall_ids": ["wall.1", "wall.2", "wall.3", "wall.4"],
            "floor_finish": None,
        },
        {
            "id": "room.hall",
            "name": "Hall",
            "wall_ids": ["wall.5", "wall.6", "wall.7", "wall.8"],
            "floor_finish": None,
        },
    ]
    return draft


def walls_only_draft() -> dict:
    """Draft with geometry but no rooms (scale known, labels absent)."""
    draft = two_room_draft()
    draft["floors"][0]["rooms"] = []
    return draft


async def _create_project(client: AsyncClient, headers: dict) -> str:
    response = await client.post(
        "/api/v1/projects", json={"name": "Setup project"}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _upload_asset(
    client: AsyncClient, headers: dict, project_id: str, role: str
) -> str:
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        data={"role": role},
        files={"file": ("image.png", png_bytes(), "image/png")},
    )
    assert upload.status_code == 201
    return upload.json()["id"]


async def _save_draft(
    client: AsyncClient, headers: dict, project_id: str, draft: dict
) -> None:
    saved = await client.put(
        f"/api/v1/projects/{project_id}/plan/draft",
        headers=headers,
        json={"draft": draft},
    )
    assert saved.status_code == 200, saved.text


async def _commit_draft(client: AsyncClient, headers: dict, project_id: str) -> None:
    commit = await client.post(
        f"/api/v1/projects/{project_id}/plan/draft/commit",
        headers=headers,
    )
    assert commit.status_code == 200, commit.text


async def _get_setup(client: AsyncClient, project_id: str) -> dict:
    response = await client.get(f"/api/v1/projects/{project_id}/setup")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_setup_empty_project_returns_upload_plan_step(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            body = await _get_setup(client, project_id)
            assert body["project_id"] == project_id
            assert body["flags"] == {
                "plan_uploaded": False,
                "scale_known": False,
                "geometry_draft": False,
                "geometry_confirmed": False,
                "room_labels": False,
                "photos_added": False,
                "photo_mapping": False,
                "ready_for_design": False,
            }
            assert body["current_step"] == {
                "id": "upload_plan",
                "number": 1,
                "label": "Загрузить план",
            }
            assert body["next_action"]["id"] == "upload_plan"
            assert body["next_action"]["page"] == "plan"
            assert body["next_action"]["disabled"] is False
            assert body["what_stroy_knows"] == []
            assert body["must_confirm"]
            assert body["diagnostics"] == {
                "plan_asset_count": 0,
                "legacy_apartment_asset_count": 0,
                "room_count": 0,
                "room_photo_attachment_count": 0,
                "dangling_room_photo_attachment_count": 0,
            }


@pytest.mark.asyncio
async def test_setup_legacy_apartment_asset_counts_as_plan_uploaded(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "apartment")

            body = await _get_setup(client, project_id)
            assert body["flags"]["plan_uploaded"] is True
            assert body["diagnostics"]["legacy_apartment_asset_count"] == 1
            assert body["diagnostics"]["plan_asset_count"] == 0
            assert body["current_step"]["id"] == "check_rooms_scale"
            assert body["next_action"]["id"] == "review_rooms"


@pytest.mark.asyncio
async def test_setup_plan_role_counts_as_plan_uploaded(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")

            body = await _get_setup(client, project_id)
            assert body["flags"]["plan_uploaded"] is True
            assert body["diagnostics"]["plan_asset_count"] == 1
            assert body["diagnostics"]["legacy_apartment_asset_count"] == 0


@pytest.mark.asyncio
async def test_setup_photo_role_counts_as_photos_added_not_plan(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "photo")

            body = await _get_setup(client, project_id)
            assert body["flags"]["photos_added"] is True
            assert body["flags"]["plan_uploaded"] is False
            assert body["diagnostics"]["plan_asset_count"] == 0
            assert body["diagnostics"]["legacy_apartment_asset_count"] == 0
            # Photos without a plan: the journey still starts at the upload.
            assert body["current_step"]["id"] == "upload_plan"


@pytest.mark.asyncio
async def test_setup_scale_known_from_latest_draft(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")

            body = await _get_setup(client, project_id)
            assert body["flags"]["scale_known"] is False

            await _save_draft(
                client,
                headers,
                project_id,
                two_room_draft(scale={"source": "manual", "mm_per_px": 1.0}),
            )
            body = await _get_setup(client, project_id)
            assert body["flags"]["scale_known"] is True
            assert body["flags"]["geometry_draft"] is True
            assert body["current_step"]["id"] == "create_3d"
            assert body["next_action"]["id"] == "commit_geometry"

            # A newer draft wins: unknown scale retracts the flag.
            await _save_draft(
                client,
                headers,
                project_id,
                two_room_draft(scale={"source": "unknown", "mm_per_px": None}),
            )
            body = await _get_setup(client, project_id)
            assert body["flags"]["scale_known"] is False
            assert body["current_step"]["id"] == "check_rooms_scale"


@pytest.mark.asyncio
async def test_setup_geometry_draft_without_commit_not_confirmed(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")
            await _save_draft(client, headers, project_id, two_room_draft())

            body = await _get_setup(client, project_id)
            assert body["flags"]["geometry_draft"] is True
            assert body["flags"]["geometry_confirmed"] is False
            assert body["current_step"]["id"] == "create_3d"
            assert body["next_action"]["id"] == "commit_geometry"
            assert body["next_action"]["disabled"] is False
            assert body["diagnostics"]["room_count"] == 0


@pytest.mark.asyncio
async def test_setup_committed_draft_sets_geometry_confirmed(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")
            await _save_draft(client, headers, project_id, two_room_draft())
            await _commit_draft(client, headers, project_id)

            body = await _get_setup(client, project_id)
            assert body["flags"]["geometry_confirmed"] is True
            assert body["flags"]["geometry_draft"] is True
            assert body["flags"]["scale_known"] is True
            assert body["diagnostics"]["room_count"] == 2
            # Photos are still missing: the journey points at add_photos.
            assert body["current_step"]["id"] == "add_photos"
            assert body["next_action"]["id"] == "add_photos"
            assert body["next_action"]["page"] == "design"
            assert body["flags"]["ready_for_design"] is False


@pytest.mark.asyncio
async def test_setup_demo_scene_does_not_confirm_geometry(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            # Demo/golden room scene: rooms in the scene, but no plan draft.
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.demo",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "room.living",
                            "kind": "room",
                            "state": "structure",
                            "display_name": "Living room",
                            "locks": {},
                        }
                    ],
                    "cameras": [],
                },
            )
            assert scene.status_code == 201

            body = await _get_setup(client, project_id)
            assert body["flags"]["geometry_confirmed"] is False
            assert body["flags"]["geometry_draft"] is False
            assert body["flags"]["room_labels"] is False
            assert body["diagnostics"]["room_count"] == 1
            # Without a committed draft the journey starts at the beginning.
            assert body["current_step"]["id"] == "upload_plan"


@pytest.mark.asyncio
async def test_setup_corrupt_scene_json_degrades(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.demo",
                    "project_id": project_id,
                    "entities": [
                        {"id": "room.living", "kind": "room", "locks": {}}
                    ],
                    "cameras": [],
                },
            )
            assert scene.status_code == 201
            revision_id = scene.json()["revision_id"]

            # Corrupt the stored revision snapshot directly (read-only safety:
            # setup must degrade to "no canonical scene", not 500).
            async with app.state.session_factory() as db:
                revision = await db.get(SceneRevisionRow, revision_id)
                assert revision is not None
                revision.scene_json = {"entities": []}  # fails Scene validation
                await db.commit()

            body = await _get_setup(client, project_id)
            # Asset-derived knowledge is unaffected by the corrupt scene.
            assert body["flags"]["plan_uploaded"] is True
            # Scene-derived state degrades exactly like a missing scene.
            assert body["flags"]["geometry_confirmed"] is False
            assert body["flags"]["geometry_draft"] is False
            assert body["flags"]["room_labels"] is False
            assert body["flags"]["photos_added"] is False
            assert body["flags"]["photo_mapping"] is False
            assert body["diagnostics"]["room_count"] == 0
            assert body["diagnostics"]["room_photo_attachment_count"] == 0
            assert body["diagnostics"]["dangling_room_photo_attachment_count"] == 0
            assert body["current_step"]["id"] == "check_rooms_scale"


@pytest.mark.asyncio
async def test_setup_room_labels_from_draft_and_scene(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")

            # Walls without rooms: geometry exists, but there are no labels.
            await _save_draft(client, headers, project_id, walls_only_draft())
            body = await _get_setup(client, project_id)
            assert body["flags"]["geometry_draft"] is True
            assert body["flags"]["room_labels"] is False

            # Draft rooms carry names -> labels known before any commit.
            await _save_draft(client, headers, project_id, two_room_draft())
            body = await _get_setup(client, project_id)
            assert body["flags"]["room_labels"] is True

            # After the commit the labels come from the canonical scene.
            await _commit_draft(client, headers, project_id)
            body = await _get_setup(client, project_id)
            assert body["flags"]["geometry_confirmed"] is True
            assert body["flags"]["room_labels"] is True
            assert "У комнат есть названия" in body["what_stroy_knows"]


@pytest.mark.asyncio
async def test_setup_room_photo_attachment_sets_photo_mapping(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")
            await _save_draft(client, headers, project_id, two_room_draft())
            await _commit_draft(client, headers, project_id)

            photo_id = await _upload_asset(client, headers, project_id, "photo")
            attachment = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "room",
                    "target_id": "room.living",
                    "kind": "photo",
                    "asset_id": photo_id,
                },
            )
            assert attachment.status_code == 201, attachment.text

            body = await _get_setup(client, project_id)
            assert body["flags"]["photo_mapping"] is True
            assert body["diagnostics"]["room_photo_attachment_count"] == 1
            assert body["diagnostics"]["dangling_room_photo_attachment_count"] == 0

            # Every flag is now met: the journey is complete.
            seven_flags = (
                "plan_uploaded",
                "scale_known",
                "geometry_draft",
                "geometry_confirmed",
                "room_labels",
                "photos_added",
                "photo_mapping",
            )
            assert all(body["flags"][key] for key in seven_flags)
            assert body["flags"]["ready_for_design"] is True
            assert body["current_step"]["id"] == "ready"
            assert body["next_action"]["id"] == "open_design"
            assert body["next_action"]["page"] == "design"
            assert body["must_confirm"] == []


@pytest.mark.asyncio
async def test_setup_dangling_room_photo_attachment_does_not_set_mapping(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _upload_asset(client, headers, project_id, "plan")
            await _save_draft(client, headers, project_id, two_room_draft())
            await _commit_draft(client, headers, project_id)

            photo_id = await _upload_asset(client, headers, project_id, "photo")
            attachment = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "room",
                    "target_id": "room.living",
                    "kind": "photo",
                    "asset_id": photo_id,
                },
            )
            assert attachment.status_code == 201, attachment.text

            # A plan recommit replaces the scene and retires room.living, so
            # the attachment survives with a dangling target (documented
            # no-FK semantics on attachments.target_id).
            await _save_draft(client, headers, project_id, renamed_rooms_draft())
            await _commit_draft(client, headers, project_id)

            body = await _get_setup(client, project_id)
            assert body["flags"]["photo_mapping"] is False
            assert body["flags"]["geometry_confirmed"] is True
            assert body["diagnostics"]["room_photo_attachment_count"] == 0
            assert body["diagnostics"]["dangling_room_photo_attachment_count"] == 1
            assert body["current_step"]["id"] == "map_photos"
            assert body["next_action"]["id"] == "map_photos"
            assert body["next_action"]["page"] == "design"
            assert body["next_action"]["disabled"] is False


@pytest.mark.asyncio
async def test_asset_upload_accepts_plan_and_photo_roles(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            for role in ("plan", "photo", "apartment"):
                upload = await client.post(
                    f"/api/v1/projects/{project_id}/assets",
                    headers=headers,
                    data={"role": role},
                    files={"file": (f"{role}.png", png_bytes(), "image/png")},
                )
                assert upload.status_code == 201
                assert upload.json()["role"] == role

            rejected = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "bogus"},
                files={"file": ("x.png", png_bytes(), "image/png")},
            )
            assert rejected.status_code == 422
