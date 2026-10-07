"""Attachments API (#160): CRUD, validation, filters, cascade."""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import event, func, select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import AttachmentRow, ProjectRow
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
    Image.new("RGB", (3, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _enable_sqlite_foreign_keys(app) -> None:
    """Enforce FKs on every connection so a broken cascade order surfaces."""
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
    """Room + furniture scene; returns the base revision id."""
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
    return response.json()["revision_id"]


@pytest.mark.asyncio
async def test_attachment_crud_note_on_project(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            created = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "kind": "note",
                    "body": "measurement pending",
                    "metadata": {"source": "walkthrough"},
                },
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["project_id"] == project_id
            assert body["target_type"] == "project"
            assert body["target_id"] is None
            assert body["kind"] == "note"
            assert body["asset_id"] is None
            assert body["done"] is False
            assert body["due_date"] is None
            assert body["metadata"] == {"source": "walkthrough"}
            assert body["id"] and body["created_at"] and body["updated_at"]

            listed = await client.get(
                f"/api/v1/projects/{project_id}/attachments", headers={"X-Noop": "1"}
            )
            assert listed.status_code == 200
            assert [row["id"] for row in listed.json()] == [body["id"]]

            patched = await client.patch(
                f"/api/v1/projects/{project_id}/attachments/{body['id']}",
                headers=headers,
                json={"body": "measurement done"},
            )
            assert patched.status_code == 200
            assert patched.json()["body"] == "measurement done"
            assert patched.json()["done"] is False

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}/attachments/{body['id']}",
                headers=headers,
            )
            assert deleted.status_code == 204

            listed = await client.get(f"/api/v1/projects/{project_id}/attachments")
            assert listed.json() == []

            again = await client.delete(
                f"/api/v1/projects/{project_id}/attachments/{body['id']}",
                headers=headers,
            )
            assert again.status_code == 404


@pytest.mark.asyncio
async def test_task_on_room_with_due_date(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            created = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "room",
                    "target_id": "room.living",
                    "kind": "task",
                    "body": "order laminate",
                    "due_date": "2026-10-20",
                },
            )
            assert created.status_code == 201, created.text
            body = created.json()
            assert body["target_id"] == "room.living"
            assert body["kind"] == "task"
            assert body["due_date"] == "2026-10-20"
            assert body["asset_id"] is None  # tasks carry no asset

            tasks = await client.get(
                f"/api/v1/projects/{project_id}/attachments",
                params={"kind": "task"},
            )
            assert [row["id"] for row in tasks.json()] == [body["id"]]


@pytest.mark.asyncio
async def test_patch_done_due_date_and_metadata(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            created = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "kind": "task",
                    "body": "check sockets",
                    "metadata": {"priority": 1},
                },
            )
            attachment_id = created.json()["id"]

            patched = await client.patch(
                f"/api/v1/projects/{project_id}/attachments/{attachment_id}",
                headers=headers,
                json={"done": True, "due_date": "2026-11-01", "metadata": {"priority": 2}},
            )
            assert patched.status_code == 200
            body = patched.json()
            assert body["done"] is True
            assert body["due_date"] == "2026-11-01"
            assert body["metadata"] == {"priority": 2}
            assert body["body"] == "check sockets"  # untouched field stays

            # due_date can be cleared explicitly.
            cleared = await client.patch(
                f"/api/v1/projects/{project_id}/attachments/{attachment_id}",
                headers=headers,
                json={"due_date": None},
            )
            assert cleared.json()["due_date"] is None

            # No retargeting in R1: immutable fields are ignored, not applied.
            retarget = await client.patch(
                f"/api/v1/projects/{project_id}/attachments/{attachment_id}",
                headers=headers,
                json={"target_type": "room", "target_id": "room.x", "kind": "photo"},
            )
            assert retarget.status_code == 200
            assert retarget.json()["target_type"] == "project"
            assert retarget.json()["target_id"] is None
            assert retarget.json()["kind"] == "task"


@pytest.mark.asyncio
async def test_unknown_attachment_returns_404(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            patched = await client.patch(
                f"/api/v1/projects/{project_id}/attachments/{uuid4()}",
                headers=headers,
                json={"done": True},
            )
            assert patched.status_code == 404

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}/attachments/{uuid4()}",
                headers=headers,
            )
            assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_unknown_project_returns_404(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            missing = str(uuid4())

            listed = await client.get(f"/api/v1/projects/{missing}/attachments")
            assert listed.status_code == 404

            created = await client.post(
                f"/api/v1/projects/{missing}/attachments",
                headers=headers,
                json={"target_type": "project", "kind": "note"},
            )
            assert created.status_code == 404


@pytest.mark.asyncio
async def test_asset_from_other_project_is_rejected(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_a = await _create_project(client, headers, "A")
            project_b = await _create_project(client, headers, "B")

            uploaded = await client.post(
                f"/api/v1/projects/{project_a}/assets",
                headers=headers,
                data={"role": "attachment"},
                files={"file": ("room.png", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201
            asset_id = uploaded.json()["id"]

            foreign = await client.post(
                f"/api/v1/projects/{project_b}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "kind": "photo",
                    "asset_id": asset_id,
                },
            )
            assert foreign.status_code == 422
            assert foreign.json()["detail"]["code"] == "invalid_attachment_asset"

            unknown = await client.post(
                f"/api/v1/projects/{project_a}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "kind": "photo",
                    "asset_id": str(uuid4()),
                },
            )
            assert unknown.status_code == 422
            assert unknown.json()["detail"]["code"] == "invalid_attachment_asset"


@pytest.mark.asyncio
async def test_photo_and_file_without_asset_rejected(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            for kind in ("photo", "file"):
                response = await client.post(
                    f"/api/v1/projects/{project_id}/attachments",
                    headers=headers,
                    json={"target_type": "project", "kind": kind},
                )
                assert response.status_code == 422, kind


@pytest.mark.asyncio
async def test_note_and_task_without_asset_accepted(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            for kind in ("note", "task"):
                response = await client.post(
                    f"/api/v1/projects/{project_id}/attachments",
                    headers=headers,
                    json={"target_type": "project", "kind": kind},
                )
                assert response.status_code == 201, kind


@pytest.mark.asyncio
async def test_unknown_kind_and_target_type_rejected(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            bad_kind = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={"target_type": "project", "kind": "link"},
            )
            assert bad_kind.status_code == 422

            bad_target = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={"target_type": "camera", "kind": "note"},
            )
            assert bad_target.status_code == 422

            project_with_id = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "target_id": "room.living",
                    "kind": "note",
                },
            )
            assert project_with_id.status_code == 422

            room_without_id = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={"target_type": "room", "kind": "note"},
            )
            assert room_without_id.status_code == 422


@pytest.mark.asyncio
async def test_target_must_exist_in_current_scene(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            # Room target must be a room-kind entity.
            furniture_as_room = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "room",
                    "target_id": "object.sofa.main",
                    "kind": "note",
                },
            )
            assert furniture_as_room.status_code == 422
            assert furniture_as_room.json()["detail"]["code"] == "invalid_room_target"

            # Unknown room / entity ids are rejected.
            unknown_room = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "room",
                    "target_id": "room.missing",
                    "kind": "note",
                },
            )
            assert unknown_room.status_code == 422
            assert unknown_room.json()["detail"]["code"] == "unknown_room"

            unknown_entity = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "entity",
                    "target_id": "object.missing",
                    "kind": "note",
                },
            )
            assert unknown_entity.status_code == 422
            assert unknown_entity.json()["detail"]["code"] == "unknown_entity"

            # Valid entity target passes.
            ok = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "entity",
                    "target_id": "object.sofa.main",
                    "kind": "note",
                    "body": "sofa note",
                },
            )
            assert ok.status_code == 201


@pytest.mark.asyncio
async def test_dangling_target_still_lists(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id = await _init_scene(client, headers, project_id)

            created = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "entity",
                    "target_id": "object.sofa.main",
                    "kind": "note",
                    "body": "attached to sofa",
                },
            )
            assert created.status_code == 201

            # Remove the target entity from the scene: the attachment survives
            # as dangling and must still be listed as-is.
            removed = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": f"command-{uuid4()}",
                    "base_revision_id": revision_id,
                    "operation": "remove_object",
                    "target_id": "object.sofa.main",
                    "parameters": {},
                    "origin": "user",
                },
            )
            assert removed.status_code == 200

            listed = await client.get(f"/api/v1/projects/{project_id}/attachments")
            assert listed.status_code == 200
            rows = listed.json()
            assert len(rows) == 1
            assert rows[0]["target_type"] == "entity"
            assert rows[0]["target_id"] == "object.sofa.main"


@pytest.mark.asyncio
async def test_attachment_filters(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            ids = {}
            for payload in (
                {"target_type": "project", "kind": "note"},
                {
                    "target_type": "room",
                    "target_id": "room.living",
                    "kind": "task",
                    "due_date": "2026-10-20",
                },
                {
                    "target_type": "entity",
                    "target_id": "object.sofa.main",
                    "kind": "note",
                },
                {
                    "target_type": "entity",
                    "target_id": "object.sofa.main",
                    "kind": "task",
                },
            ):
                response = await client.post(
                    f"/api/v1/projects/{project_id}/attachments",
                    headers=headers,
                    json=payload,
                )
                assert response.status_code == 201, response.text
                ids[f"{payload['target_type']}:{payload['kind']}"] = response.json()["id"]

            base = f"/api/v1/projects/{project_id}/attachments"

            by_type = (await client.get(base, params={"target_type": "entity"})).json()
            assert {row["id"] for row in by_type} == {
                ids["entity:note"],
                ids["entity:task"],
            }

            by_kind = (await client.get(base, params={"kind": "task"})).json()
            assert {row["id"] for row in by_kind} == {
                ids["room:task"],
                ids["entity:task"],
            }

            by_target = (
                await client.get(
                    base,
                    params={"target_type": "entity", "target_id": "object.sofa.main"},
                )
            ).json()
            assert {row["id"] for row in by_target} == {
                ids["entity:note"],
                ids["entity:task"],
            }

            combined = (
                await client.get(
                    base, params={"target_type": "room", "target_id": "room.living", "kind": "task"}
                )
            ).json()
            assert [row["id"] for row in combined] == [ids["room:task"]]


@pytest.mark.asyncio
async def test_asset_role_attachment_accepted(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "attachment"},
                files={"file": ("scan.pdf", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201
            assert uploaded.json()["role"] == "attachment"

            rejected = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "bogus"},
                files={"file": ("x.png", png_bytes(), "image/png")},
            )
            assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_delete_project_cascades_attachments(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    _enable_sqlite_foreign_keys(app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers, "Doomed")

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "attachment"},
                files={"file": ("room.png", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201
            asset_id = uploaded.json()["id"]

            photo = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "kind": "photo",
                    "asset_id": asset_id,
                },
            )
            assert photo.status_code == 201
            note = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={"target_type": "project", "kind": "note"},
            )
            assert note.status_code == 201

            deleted = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
            assert deleted.status_code == 204, deleted.text

            async with app.state.session_factory() as db:
                assert await db.get(ProjectRow, project_id) is None
                assert await db.get(AttachmentRow, photo.json()["id"]) is None
                remaining = await db.scalar(
                    select(func.count())
                    .select_from(AttachmentRow)
                    .where(AttachmentRow.project_id == project_id)
                )
                assert remaining == 0


@pytest.mark.asyncio
async def test_attachment_auth_owner_and_csrf(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            # Reads require an owner session: a client with no login cookie
            # (separate cookie jar) gets 401.
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as anonymous:
                unauthenticated = await anonymous.get(
                    f"/api/v1/projects/{project_id}/attachments"
                )
            assert unauthenticated.status_code == 401

            # Writes require the CSRF token on top of the session.
            csrfless = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                json={"target_type": "project", "kind": "note"},
            )
            assert csrfless.status_code == 403

            ok = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={"target_type": "project", "kind": "note"},
            )
            assert ok.status_code == 201
