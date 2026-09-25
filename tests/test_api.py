from __future__ import annotations

from io import BytesIO

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import DesignCommandRow
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


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.mark.asyncio
async def test_auth_scene_revision_and_worker_flow(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            health = await client.get("/health", headers={"X-Request-ID": "test-request"})
            assert health.status_code == 200
            assert health.headers["X-Request-ID"] == "test-request"
            assert (await client.get("/api/v1/projects")).status_code == 401

            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            project_response = await client.post(
                "/api/v1/projects", json={"name": "Apartment"}, headers=headers
            )
            assert project_response.status_code == 201
            project_id = project_response.json()["id"]

            scene_response = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.main",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "object.sofa.main",
                            "kind": "furniture",
                            "locks": {},
                        }
                    ],
                    "cameras": [],
                },
            )
            assert scene_response.status_code == 201
            revision_id = scene_response.json()["revision_id"]

            command_response = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": "command-1",
                    "base_revision_id": revision_id,
                    "operation": "set_color",
                    "target_id": "object.sofa.main",
                    "parameters": {"color": "#D7C4AB"},
                    "origin": "user",
                },
            )
            assert command_response.status_code == 200
            assert (
                command_response.json()["scene"]["entities"][0]["metadata"]["color"]
                == "#D7C4AB"
            )
            async with app.state.session_factory() as db:
                command_row = (
                    await db.execute(
                        select(DesignCommandRow).where(
                            DesignCommandRow.id == "command-1"
                        )
                    )
                ).scalar_one()
                assert command_row.origin == "user"

            history = await client.get(
                f"/api/v1/projects/{project_id}/scene/revisions"
            )
            assert history.status_code == 200
            assert len(history.json()) == 2

            revert = await client.post(
                f"/api/v1/projects/{project_id}/scene/revert",
                headers=headers,
                json={
                    "expected_base_revision_id": command_response.json()["revision_id"],
                    "target_revision_id": revision_id,
                },
            )
            assert revert.status_code == 200
            assert "color" not in revert.json()["scene"]["entities"][0]["metadata"]

            redo = await client.post(
                f"/api/v1/projects/{project_id}/scene/revert",
                headers=headers,
                json={
                    "expected_base_revision_id": revert.json()["revision_id"],
                    "target_revision_id": command_response.json()["revision_id"],
                },
            )
            assert redo.status_code == 200
            assert (
                redo.json()["scene"]["entities"][0]["metadata"]["color"]
                == "#D7C4AB"
            )

            input_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                files={"file": ("input.png", png_bytes(), "image/png")},
            )
            assert input_upload.status_code == 201
            input_asset_id = input_upload.json()["id"]

            job_response = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=headers,
                json={
                    "job_type": "image.generate",
                    "payload": {"input_asset_ids": [input_asset_id]},
                    "required_capabilities": ["image_generation"],
                    "idempotency_key": "generate-scene-1",
                },
            )
            assert job_response.status_code == 201
            job_id = job_response.json()["id"]

            duplicate_job = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=headers,
                json={
                    "job_type": "image.generate",
                    "payload": {"input_asset_ids": [input_asset_id]},
                    "required_capabilities": ["image_generation"],
                    "idempotency_key": "generate-scene-1",
                },
            )
            assert duplicate_job.status_code == 201
            assert duplicate_job.json()["id"] == job_id

            worker_headers = {"Authorization": "Bearer worker-secret"}
            register = await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "capabilities": ["image_generation", "llm"],
                    "models": ["fake"],
                    "runtimes": {"fake": {"status": "ready"}},
                },
            )
            assert register.status_code == 200

            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-1"},
            )
            assert claim.status_code == 200
            lease = claim.json()
            assert lease["job_id"] == job_id

            input_url = lease["download_urls"][input_asset_id]
            downloaded_input = await client.get(input_url, headers=worker_headers)
            assert downloaded_input.status_code == 200
            assert downloaded_input.content == png_bytes()

            progress = await client.post(
                f"/api/v1/workers/jobs/{job_id}/progress",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "lease_id": lease["lease_id"],
                    "progress": {"phase": "sampling", "fraction": 0.5},
                    "runtime_provenance": {"runtime": "fake", "version": "0.1"},
                },
            )
            assert progress.status_code == 200
            assert progress.json()["progress"]["fraction"] == 0.5

            output = await client.post(
                f"/api/v1/workers/jobs/{job_id}/outputs",
                headers=worker_headers,
                data={
                    "worker_id": "worker-1",
                    "lease_id": lease["lease_id"],
                },
                files={"file": ("render.png", b"fake-image", "image/png")},
            )
            assert output.status_code == 201
            output_asset_id = output.json()["id"]

            assets = await client.get(f"/api/v1/projects/{project_id}/assets")
            assert assets.status_code == 200
            output_asset = next(
                item for item in assets.json() if item["id"] == output_asset_id
            )
            assert output_asset["provenance"] == "model_inferred"
            assert output_asset["role"] == "derived"
            assert output_asset["source_asset_ids"] == [input_asset_id]

            input_asset = next(
                item for item in assets.json() if item["id"] == input_asset_id
            )
            assert input_asset["metadata"]["width_px"] == 3
            assert input_asset["metadata"]["height_px"] == 2

            workers = await client.get("/api/v1/workers")
            assert workers.status_code == 200
            assert workers.json()[0]["online"] is True

            complete = await client.post(
                f"/api/v1/workers/jobs/{job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "lease_id": lease["lease_id"],
                    "result": {
                        "output_asset_ids": [output_asset_id],
                    },
                    "runtime_provenance": {
                        "runtime": "fake",
                        "version": "0.1",
                    },
                },
            )
            assert complete.status_code == 200
            assert complete.json()["status"] == "succeeded"

            design = await client.post(
                f"/api/v1/projects/{project_id}/design/instructions",
                headers=headers,
                json={"text": "Сделай диван бежевым"},
            )
            assert design.status_code == 201
            assert design.json()["job_type"] == "llm.complete"

            llm_claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-1"},
            )
            assert llm_claim.status_code == 200
            llm_lease = llm_claim.json()
            assert llm_lease["job_type"] == "llm.complete"

            llm_complete = await client.post(
                f"/api/v1/workers/jobs/{llm_lease['job_id']}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "lease_id": llm_lease["lease_id"],
                    "result": {
                        "tool_calls": [
                            {
                                "name": "set_color",
                                "arguments": {
                                    "target_id": "object.sofa.main",
                                    "color": "#D7C4AB",
                                },
                            }
                        ]
                    },
                },
            )
            assert llm_complete.status_code == 200
            assert llm_complete.json()["status"] == "succeeded"
            assert len(llm_complete.json()["result"]["applied_revision_ids"]) == 1

            final_scene = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert final_scene.status_code == 200
            assert (
                final_scene.json()["scene"]["entities"][0]["metadata"]["color"]
                == "#D7C4AB"
            )

            stale = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": "stale-command",
                    "base_revision_id": revision_id,
                    "operation": "set_color",
                    "target_id": "object.sofa.main",
                    "parameters": {"color": "#000000"},
                    "origin": "user",
                },
            )
            assert stale.status_code == 409


@pytest.mark.asyncio
async def test_csrf_is_required(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            await login(client)
            response = await client.post("/api/v1/projects", json={"name": "Denied"})
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_upload_policy_rejects_unsupported_type_and_oversize(settings):
    restricted = settings.model_copy(update={"max_upload_bytes": 4})
    app = create_app(settings=restricted, object_store=MemoryObjectStore())
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
                json={"name": "Upload policy"},
            )
            project_id = project.json()["id"]

            unsupported = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                files={"file": ("payload.xyz", b"abc", "application/x-unknown")},
            )
            assert unsupported.status_code == 415

            too_large = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                files={"file": ("photo.png", b"12345", "image/png")},
            )
            assert too_large.status_code == 413


@pytest.mark.asyncio
async def test_job_cancel_invalidates_worker_lease(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            owner_headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects",
                headers=owner_headers,
                json={"name": "Cancel"},
            )
            project_id = project.json()["id"]

            job = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=owner_headers,
                json={
                    "job_type": "render.blender",
                    "payload": {},
                    "required_capabilities": ["blender_render"],
                },
            )
            job_id = job.json()["id"]

            worker_headers = {"Authorization": "Bearer worker-secret"}
            await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "worker-cancel",
                    "capabilities": ["blender_render"],
                },
            )
            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-cancel"},
            )
            lease = claim.json()

            cancelled = await client.post(
                f"/api/v1/jobs/{job_id}/cancel",
                headers=owner_headers,
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"

            late_complete = await client.post(
                f"/api/v1/workers/jobs/{job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-cancel",
                    "lease_id": lease["lease_id"],
                    "result": {},
                },
            )
            assert late_complete.status_code == 409
