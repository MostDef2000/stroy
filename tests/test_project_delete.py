from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import IntegrityError

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import (
    AssetRow,
    DesignCommandRow,
    GenerationManifestRow,
    GeometryDiagnosticRow,
    JobRow,
    PlanDraftRow,
    ProjectRow,
    RenderManifestRow,
    SceneRevisionRow,
    StyleProfileRow,
)
from stroy.services.assets import MemoryObjectStore


PROJECT_SCOPED_TABLES = (
    SceneRevisionRow,
    DesignCommandRow,
    AssetRow,
    StyleProfileRow,
    PlanDraftRow,
    GenerationManifestRow,
    RenderManifestRow,
    GeometryDiagnosticRow,
    JobRow,
)


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
    """Enforce foreign keys on every connection this app's engine opens.

    SQLite disables FK enforcement by default, so a cascade that deletes a
    parent before its children would silently succeed.  The listener is
    attached before the lifespan opens its first connection, so every pooled
    connection gets ``PRAGMA foreign_keys=ON`` and a bad order raises
    ``IntegrityError``.
    """
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


async def _create_project(client: AsyncClient, headers: dict, name: str) -> str:
    response = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def _seed_full_graph(app, client: AsyncClient, headers: dict) -> tuple[str, str]:
    """Create a project with one row in every project-scoped table.

    Returns ``(project_id, asset_id)``.
    """
    project_response = await client.post(
        "/api/v1/projects", json={"name": "Doomed"}, headers=headers
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    asset_response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        data={"role": "apartment"},
        files={"file": ("plan.png", png_bytes(), "image/png")},
    )
    assert asset_response.status_code == 201
    asset_id = asset_response.json()["id"]

    scene_response = await client.post(
        f"/api/v1/projects/{project_id}/scene",
        headers=headers,
        json={
            "scene_id": "scene.main",
            "project_id": project_id,
            "entities": [{"id": "object.sofa.main", "kind": "furniture", "locks": {}}],
            "cameras": [
                {
                    "id": "camera.main",
                    "width_px": 640,
                    "height_px": 400,
                    "intrinsics": {"fx": 500, "fy": 505, "cx": 320, "cy": 200},
                    "transform": {
                        "translation_mm": [0, -4000, 1600],
                        "rotation_deg": [78, 0, 0],
                    },
                }
            ],
        },
    )
    assert scene_response.status_code == 201
    revision_id = scene_response.json()["revision_id"]

    command_response = await client.post(
        f"/api/v1/projects/{project_id}/scene/commands",
        headers=headers,
        json={
            "command_id": f"command-{uuid4()}",
            "base_revision_id": revision_id,
            "operation": "set_color",
            "target_id": "object.sofa.main",
            "parameters": {"color": "#D7C4AB"},
            "origin": "user",
        },
    )
    assert command_response.status_code == 200

    stored_key = f"projects/{project_id}/direct-extra.png"
    await app.state.object_store.put_bytes(stored_key, png_bytes(), "image/png")

    async with app.state.session_factory() as db:
        db.add_all(
            [
                AssetRow(
                    project_id=project_id,
                    object_key=stored_key,
                    media_type="image/png",
                    size_bytes=1,
                    sha256="0" * 64,
                    role="derived",
                ),
                StyleProfileRow(
                    project_id=project_id,
                    source_asset_ids=[asset_id],
                    profile_json={"style": "warm"},
                ),
                PlanDraftRow(project_id=project_id, version=1, draft_json={"rooms": []}),
                GenerationManifestRow(
                    id=f"gen-{uuid4()}",
                    project_id=project_id,
                    job_id=f"job-gen-{uuid4()}",
                    scene_revision_id=revision_id,
                    design_revision_id=revision_id,
                    camera_id="camera.main",
                    manifest_json={"generation_id": "g-1"},
                ),
                RenderManifestRow(
                    id=f"render-{uuid4()}",
                    project_id=project_id,
                    job_id=f"job-render-{uuid4()}",
                    scene_revision_id=revision_id,
                    design_revision_id=revision_id,
                    camera_id="camera.main",
                    manifest_json={"render_id": "r-1"},
                ),
                GeometryDiagnosticRow(
                    id=f"diag-{uuid4()}",
                    project_id=project_id,
                    job_id=f"job-diag-{uuid4()}",
                    scene_revision_id=revision_id,
                    camera_id="camera.main",
                    reference_asset_id=asset_id,
                    generated_asset_id=asset_id,
                    diagnostic_json={"ok": True},
                ),
                # Terminal job: must NOT block deletion.
                JobRow(
                    id=f"job-done-{uuid4()}",
                    project_id=project_id,
                    job_type="render.blender",
                    status="succeeded",
                ),
            ]
        )
        await db.commit()

    return project_id, asset_id


@pytest.mark.asyncio
async def test_delete_project_happy_path(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    # Enforce FK constraints for this test: a broken delete order (project
    # removed before its children, or a referenced table removed too late)
    # must surface as IntegrityError instead of silently passing.
    _enable_sqlite_foreign_keys(app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, asset_id = await _seed_full_graph(app, client, headers)

            async with app.state.session_factory() as db:
                assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1

            response = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
            assert response.status_code == 204, response.text

            listed = await client.get("/api/v1/projects")
            assert project_id not in [row["id"] for row in listed.json()]

            assert (
                await client.get(f"/api/v1/assets/{asset_id}")
            ).status_code == 404
            assert (
                await client.get(f"/api/v1/projects/{project_id}/scene")
            ).status_code == 404

            async with app.state.session_factory() as db:
                for table in PROJECT_SCOPED_TABLES:
                    remaining = await db.scalar(
                        select(func.count())
                        .select_from(table)
                        .where(table.project_id == project_id)
                    )
                    assert remaining == 0, table.__tablename__
                assert await db.get(ProjectRow, project_id) is None


@pytest.mark.asyncio
async def test_delete_project_enforces_foreign_keys(settings):
    """The cascade must succeed with SQLite FK enforcement turned on.

    Enforcement is proven active for this engine first: deleting the project
    row while its children still reference it violates
    ``project_id -> projects.id`` and must raise ``IntegrityError``.  With the
    service's children-first order the delete then succeeds, so a regression
    that removes the project before its children fails this test.
    """
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    _enable_sqlite_foreign_keys(app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, _asset_id = await _seed_full_graph(app, client, headers)

            async with app.state.session_factory() as db:
                assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1
                with pytest.raises(IntegrityError):
                    await db.execute(
                        delete(ProjectRow).where(ProjectRow.id == project_id)
                    )
                    await db.commit()
                await db.rollback()

            response = await client.delete(
                f"/api/v1/projects/{project_id}", headers=headers
            )
            assert response.status_code == 204, response.text

            async with app.state.session_factory() as db:
                for table in PROJECT_SCOPED_TABLES:
                    remaining = await db.scalar(
                        select(func.count())
                        .select_from(table)
                        .where(table.project_id == project_id)
                    )
                    assert remaining == 0, table.__tablename__
                assert await db.get(ProjectRow, project_id) is None


@pytest.mark.asyncio
async def test_delete_project_unknown_returns_404(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            response = await client.delete(
                f"/api/v1/projects/{uuid4()}", headers=headers
            )
            assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_blocks_on_active_job(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers, "Busy")

            created = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=headers,
                json={"job_type": "render.blender", "payload": {}},
            )
            assert created.status_code == 201
            assert created.json()["status"] == "queued"

            blocked = await client.delete(
                f"/api/v1/projects/{project_id}", headers=headers
            )
            assert blocked.status_code == 409, blocked.text

            # A running job must block just the same.
            async with app.state.session_factory() as db:
                job = (
                    await db.execute(select(JobRow).where(JobRow.project_id == project_id))
                ).scalar_one()
                job.status = "running"
                await db.commit()

            running = await client.delete(
                f"/api/v1/projects/{project_id}", headers=headers
            )
            assert running.status_code == 409, running.text

            listed = await client.get("/api/v1/projects")
            assert project_id in [row["id"] for row in listed.json()]

            # Once the job is terminal the project can be deleted.
            async with app.state.session_factory() as db:
                job = (
                    await db.execute(select(JobRow).where(JobRow.project_id == project_id))
                ).scalar_one()
                job.status = "cancelled"
                await db.commit()

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}", headers=headers
            )
            assert deleted.status_code == 204, deleted.text


@pytest.mark.asyncio
async def test_delete_project_removes_stored_files(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers, "Files")

            uploaded = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "apartment"},
                files={"file": ("plan.png", png_bytes(), "image/png")},
            )
            assert uploaded.status_code == 201

            prefix = f"projects/{project_id}/"
            assert any(key.startswith(prefix) for key in app.state.object_store.objects)

            response = await client.delete(
                f"/api/v1/projects/{project_id}", headers=headers
            )
            assert response.status_code == 204, response.text
            assert not [
                key for key in app.state.object_store.objects if key.startswith(prefix)
            ]
