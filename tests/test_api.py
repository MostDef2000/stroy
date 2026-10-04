from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import (
    AssetRow,
    DesignCommandRow,
    GenerationManifestRow,
    JobRow,
    ProjectRow,
    RenderManifestRow,
)
from stroy.editing.mask import render_replacement_mask
from stroy.editing.replacement import ReplacementRegion
from stroy.generation import WorkflowManifest
from stroy.security import sha256_text
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
                        },
                        {
                            "id": "surface.wall.living.north",
                            "kind": "wall",
                            "locks": {
                                "geometry": True,
                                "transform": True,
                            },
                        }
                    ],
                    "cameras": [
                        {
                            "id": "camera.main",
                            "width_px": 640,
                            "height_px": 400,
                            "intrinsics": {
                                "fx": 500,
                                "fy": 505,
                                "cx": 320,
                                "cy": 200
                            },
                            "transform": {
                                "translation_mm": [0, -4000, 1600],
                                "rotation_deg": [78, 0, 0]
                            }
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

            locked = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "command_id": "locked-command",
                    "base_revision_id": command_response.json()["revision_id"],
                    "operation": "move_object",
                    "target_id": "surface.wall.living.north",
                    "parameters": {"translation_mm": [10, 0, 0]},
                    "origin": "agent",
                },
            )
            assert locked.status_code == 409
            assert locked.json()["detail"]["code"] == "command_rejected"
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

            duplicate_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("duplicate.png", png_bytes(), "image/png")},
            )
            assert duplicate_upload.status_code == 201
            assert (
                duplicate_upload.json()["duplicate_of_asset_id"]
                == input_asset_id
            )

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

            design_correlation_id = design.json()["correlation_id"]
            assert design_correlation_id

            llm_claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-1"},
            )
            assert llm_claim.status_code == 200
            llm_lease = llm_claim.json()
            assert llm_lease["job_type"] == "llm.complete"

            assert llm_lease["payload"]["model_profile"] == "qwen3-14b"

            llm_complete = await client.post(
                f"/api/v1/workers/jobs/{llm_lease['job_id']}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "lease_id": llm_lease["lease_id"],
                    "result": {
                        "tool_calls": [
                            {
                                "name": "get_entity",
                                "arguments": {
                                    "target_id": "object.sofa.main",
                                },
                            },
                            {
                                "name": "set_color",
                                "arguments": {
                                    "target_id": "object.sofa.main",
                                    "color": "#D7C4AB",
                                },
                            },
                            {
                                "name": "create_design_revision",
                                "arguments": {"label": "beige sofa"},
                            },
                            {
                                "name": "render_preview",
                                "arguments": {},
                            },
                        ]
                    },
                },
            )
            assert llm_complete.status_code == 200
            assert llm_complete.json()["status"] == "succeeded"
            assert len(llm_complete.json()["result"]["applied_revision_ids"]) == 1

            agent_result = llm_complete.json()["result"]
            assert len(agent_result["preview_job_ids"]) == 1
            assert [item["name"] for item in agent_result["tool_results"]] == [
                "get_entity",
                "create_design_revision",
                "render_preview",
            ]

            assert len(agent_result["generation_job_ids"]) == 1
            auto_generation_job_id = agent_result["generation_job_ids"][0]
            auto_generation = await client.get(
                f"/api/v1/jobs/{auto_generation_job_id}"
            )
            assert auto_generation.status_code == 200
            assert auto_generation.json()["job_type"] == "image.generate"
            assert (
                auto_generation.json()["progress"] == {}
                or auto_generation.json()["status"] == "waiting_for_worker"
            )
            cancelled_generation = await client.post(
                f"/api/v1/jobs/{auto_generation_job_id}/cancel",
                headers=headers,
            )
            assert cancelled_generation.status_code == 200
            assert cancelled_generation.json()["status"] == "cancelled"

            async with app.state.session_factory() as db:
                agent_command = (
                    await db.execute(
                        select(DesignCommandRow).where(
                            DesignCommandRow.origin == "agent"
                        )
                    )
                ).scalar_one()
                assert agent_command.model_profile == "qwen3-14b"
                assert agent_command.correlation_id == design_correlation_id

            final_scene = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert final_scene.status_code == 200
            assert (
                final_scene.json()["scene"]["entities"][0]["metadata"]["color"]
                == "#D7C4AB"
            )

            revision_before_bad_agent = final_scene.json()["revision_id"]
            bad_design = await client.post(
                f"/api/v1/projects/{project_id}/design/instructions",
                headers=headers,
                json={
                    "text": "inspect missing entity through agent",
                    "idempotency_key": "bad-agent-read-1",
                },
            )
            assert bad_design.status_code == 201

            bad_claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-1"},
            )
            assert bad_claim.status_code == 200
            bad_lease = bad_claim.json()
            assert bad_lease["job_type"] == "llm.complete"

            bad_complete = await client.post(
                f"/api/v1/workers/jobs/{bad_lease['job_id']}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "lease_id": bad_lease["lease_id"],
                    "result": {
                        "tool_calls": [
                            {
                                "name": "get_entity",
                                "arguments": {
                                    "target_id": "object.does.not.exist",
                                },
                            }
                        ]
                    },
                },
            )
            assert bad_complete.status_code == 200
            assert bad_complete.json()["status"] == "failed"
            assert bad_complete.json()["error"]["code"] == "unknown_entity"
            assert (
                bad_complete.json()["error"]["context"]["entity_id"]
                == "object.does.not.exist"
            )

            scene_after_bad_agent = await client.get(
                f"/api/v1/projects/{project_id}/scene"
            )
            assert (
                scene_after_bad_agent.json()["revision_id"]
                == revision_before_bad_agent
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


@pytest.mark.asyncio
async def test_style_profile_analysis_flow(settings):
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
                json={"name": "Style project"},
            )
            project_id = project.json()["id"]

            asset_ids = []
            for index in range(3):
                upload = await client.post(
                    f"/api/v1/projects/{project_id}/assets",
                    headers=headers,
                    data={"role": "reference"},
                    files={
                        "file": (
                            f"reference-{index}.png",
                            png_bytes() + bytes([index]),
                            "image/png",
                        )
                    },
                )
                assert upload.status_code == 201
                asset_ids.append(upload.json()["id"])

            analyze = await client.post(
                f"/api/v1/projects/{project_id}/style-profiles/analyze",
                headers=headers,
                json={
                    "source_text": "warm minimal interior with wood",
                    "reference_asset_ids": asset_ids,
                    "overrides": {
                        "labels": ["japandi"],
                        "palette": [{"hex": "#efe7dc", "role": "base"}],
                        "lighting": {
                            "temperature_k": 2900,
                            "intent": ["soft", "ambient"],
                        },
                    },
                },
            )
            assert analyze.status_code == 201
            job_id = analyze.json()["id"]

            worker_headers = {"Authorization": "Bearer worker-secret"}
            await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "style-worker",
                    "capabilities": ["style_analysis"],
                    "models": ["fake"],
                },
            )
            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "style-worker"},
            )
            lease = claim.json()
            assert lease["job_id"] == job_id
            assert set(lease["input_asset_ids"]) == set(asset_ids)

            complete = await client.post(
                f"/api/v1/workers/jobs/{job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "style-worker",
                    "lease_id": lease["lease_id"],
                    "result": {
                        "style_profile": {
                            "labels": ["industrial"],
                            "palette": [{"hex": "#111111", "role": "base"}],
                            "materials": [
                                {
                                    "name": "natural wood",
                                    "finish": "matte",
                                    "application": "cabinetry",
                                }
                            ],
                            "lighting": {
                                "temperature_k": 4000,
                                "intent": ["neutral"],
                            },
                            "forms": {"keywords": ["clean lines"]},
                            "negative_constraints": [],
                            "evidence": {"fixture": True},
                        },
                        "adapter_provenance": {
                            "adapter": "mock-vision",
                            "model_profile": "mock-vision-v0",
                        },
                    },
                },
            )
            assert complete.status_code == 200
            result = complete.json()["result"]
            profile = result["style_profile"]
            assert profile["style_profile_id"] == result["style_profile_id"]
            assert profile["source_asset_ids"] == asset_ids
            assert profile["source_text"] == "warm minimal interior with wood"
            assert profile["labels"] == ["japandi"]
            assert profile["palette"][0]["hex"] == "#EFE7DC"
            assert profile["lighting"]["temperature_k"] == 2900
            assert profile["materials"][0]["name"] == "natural wood"

            listed = await client.get(
                f"/api/v1/projects/{project_id}/style-profiles"
            )
            assert listed.status_code == 200
            assert len(listed.json()) == 1
            assert listed.json()[0]["profile"]["style_profile_id"] == result["style_profile_id"]
            # worker adapter provenance wins over the api payload default
            assert listed.json()[0]["model_profile"] == "mock-vision-v0"
            assert (
                listed.json()[0]["profile"]["metadata"]["adapter_provenance"]["adapter"]
                == "mock-vision"
            )



@pytest.mark.asyncio
async def test_render_job_persists_aligned_pass_manifest(settings):
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
                json={"name": "Render project"},
            )
            project_id = project.json()["id"]

            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.render",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "surface.floor.main",
                            "kind": "floor",
                            "geometry": {"dimensions_mm": [4000, 3000, 100]},
                            "locks": {"geometry": True, "transform": True},
                        }
                    ],
                    "cameras": [
                        {
                            "id": "camera.main",
                            "width_px": 800,
                            "height_px": 500,
                            "intrinsics": {
                                "fx": 620,
                                "fy": 625,
                                "cx": 400,
                                "cy": 250,
                            },
                            "transform": {
                                "translation_mm": [0, -4500, 1700],
                                "rotation_deg": [78, 0, 0],
                            },
                        }
                    ],
                },
            )
            assert scene.status_code == 201
            revision_id = scene.json()["revision_id"]

            render = await client.post(
                f"/api/v1/projects/{project_id}/renders",
                headers=headers,
                json={
                    "scene_revision_id": revision_id,
                    "camera_id": "camera.main",
                    "renderer_profile": "blender-cycles-v0",
                    "idempotency_key": "render-main-v1",
                },
            )
            assert render.status_code == 201
            render_job = render.json()
            assert render_job["job_type"] == "render.blender"

            worker_headers = {"Authorization": "Bearer worker-secret"}
            register = await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "blender-worker",
                    "capabilities": ["blender_render"],
                    "models": [],
                    "runtimes": {"blender": {"status": "ready"}},
                },
            )
            assert register.status_code == 200

            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "blender-worker"},
            )
            assert claim.status_code == 200
            lease = claim.json()
            assert lease["job_id"] == render_job["id"]
            assert lease["payload"]["scene_revision_id"] == revision_id
            assert lease["payload"]["camera_id"] == "camera.main"
            assert lease["payload"]["scene"]["scene_id"] == "scene.render"

            pass_assets = {}
            for pass_name in (
                "rgb",
                "depth",
                "normals",
                "object_ids",
                "material_ids",
            ):
                media_type = "image/png" if pass_name == "rgb" else "image/x-exr"
                suffix = "png" if pass_name == "rgb" else "exr"
                upload = await client.post(
                    f"/api/v1/workers/jobs/{render_job['id']}/outputs",
                    headers=worker_headers,
                    data={
                        "worker_id": "blender-worker",
                        "lease_id": lease["lease_id"],
                        "semantic_name": pass_name,
                    },
                    files={
                        "file": (
                            f"{pass_name}.{suffix}",
                            f"{pass_name}-bytes".encode(),
                            media_type,
                        )
                    },
                )
                assert upload.status_code == 201
                pass_assets[pass_name] = upload.json()["id"]

            complete = await client.post(
                f"/api/v1/workers/jobs/{render_job['id']}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "blender-worker",
                    "lease_id": lease["lease_id"],
                    "result": {
                        "render_manifest": {
                            "schema_version": "0.1.0",
                            "render_id": lease["payload"]["render_id"],
                            "scene_revision_id": revision_id,
                            "camera_id": "camera.main",
                            "renderer_profile": "blender-cycles-v0",
                            "passes": pass_assets,
                        },
                        # The worker sends the Blender scene metadata alongside
                        # the manifest; render_seconds must survive persistence
                        # and reach the renders API.
                        "scene_metadata": {
                            "schema_version": "0.1.0",
                            "render_seconds": 12.5,
                        },
                        "output_asset_ids": list(pass_assets.values()),
                    },
                },
            )
            assert complete.status_code == 200
            assert complete.json()["status"] == "succeeded"
            assert complete.json()["result"]["render_id"] == lease["payload"]["render_id"]

            renders = await client.get(
                f"/api/v1/projects/{project_id}/renders"
            )
            assert renders.status_code == 200
            assert len(renders.json()) == 1
            manifest = renders.json()[0]["manifest"]
            assert manifest["passes"] == pass_assets
            # render_seconds flows from scene_metadata -> manifest_json -> API.
            assert renders.json()[0]["render_seconds"] == 12.5
            assert manifest["render_seconds"] == 12.5

            render_detail = await client.get(
                f"/api/v1/renders/{lease['payload']['render_id']}"
            )
            assert render_detail.status_code == 200
            assert render_detail.json()["render_seconds"] == 12.5
            assert render_detail.json()["manifest"]["render_seconds"] == 12.5

            assets = await client.get(
                f"/api/v1/projects/{project_id}/assets"
            )
            assert assets.status_code == 200
            tagged = {
                item["metadata"].get("semantic_name"): item
                for item in assets.json()
                if item["metadata"].get("semantic_name")
            }
            assert set(pass_assets).issubset(tagged)
            assert all(tagged[name]["role"] == "derived" for name in pass_assets)


@pytest.mark.asyncio
async def test_render_api_tolerates_manifest_without_render_seconds(settings):
    """Old manifests (no scene_metadata) must still serve with render_seconds=None."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            await login(client)
            async with app.state.session_factory() as db:
                db.add(ProjectRow(id="project.legacy-render", name="Legacy render"))
                db.add(
                    RenderManifestRow(
                        id="render.legacy",
                        project_id="project.legacy-render",
                        job_id="job.legacy",
                        scene_revision_id="revision.legacy",
                        camera_id="camera.main",
                        manifest_json={
                            "schema_version": "0.1.0",
                            "render_id": "render.legacy",
                            "scene_revision_id": "revision.legacy",
                            "camera_id": "camera.main",
                            "passes": {"rgb": "asset.rgb"},
                        },
                    )
                )
                await db.commit()

            detail = await client.get("/api/v1/renders/render.legacy")
            assert detail.status_code == 200
            assert detail.json()["render_seconds"] is None
            assert "render_seconds" not in detail.json()["manifest"]

            listing = await client.get(
                "/api/v1/projects/project.legacy-render/renders"
            )
            assert listing.status_code == 200
            assert listing.json()[0]["render_seconds"] is None


@pytest.mark.asyncio
async def test_camera_crud_creates_immutable_scene_revisions(settings):
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
                json={"name": "Camera project"},
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
            assert initial.status_code == 201
            base_revision_id = initial.json()["revision_id"]

            photo = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "apartment"},
                files={"file": ("room.png", png_bytes(), "image/png")},
            )
            assert photo.status_code == 201
            photo_id = photo.json()["id"]

            update = await client.put(
                f"/api/v1/projects/{project_id}/cameras/camera.main",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "camera": {
                        "id": "camera.main",
                        "width_px": 1000,
                        "height_px": 800,
                        "intrinsics": {
                            "fx": 800,
                            "fy": 800,
                            "cx": 500,
                            "cy": 400,
                        },
                        "transform": {
                            "translation_mm": [0, -5000, 1500],
                            "rotation_deg": [90, 0, 0],
                        },
                        "source_asset_id": photo_id,
                        "provenance": {
                            "source": "measured",
                            "asset_ids": [photo_id],
                        },
                        "calibration": {
                            "method": "manual",
                            "observations": [
                                {
                                    "world_mm": [0, 0, 1500],
                                    "image_px": [500, 400],
                                    "label": "center",
                                },
                                {
                                    "world_mm": [1000, 0, 1500],
                                    "image_px": [660, 400],
                                    "label": "right",
                                },
                            ],
                        },
                    },
                },
            )
            assert update.status_code == 200
            first_revision_id = update.json()["revision_id"]
            assert first_revision_id != base_revision_id
            camera = update.json()["camera"]
            assert camera["source_asset_id"] == photo_id
            assert camera["calibration"]["method"] == "correspondences"
            assert camera["calibration"]["residual"] == pytest.approx(0.0)
            assert camera["calibration"]["quality"] == pytest.approx(1.0)

            fetched = await client.get(
                f"/api/v1/projects/{project_id}/cameras/camera.main"
            )
            assert fetched.status_code == 200
            assert fetched.json()["revision_id"] == first_revision_id
            assert fetched.json()["camera"]["source_asset_id"] == photo_id

            stale = await client.put(
                f"/api/v1/projects/{project_id}/cameras/camera.main",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "camera": camera,
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "revision_conflict"

            removed = await client.request(
                "DELETE",
                f"/api/v1/projects/{project_id}/cameras/camera.main",
                headers=headers,
                json={"base_revision_id": first_revision_id},
            )
            assert removed.status_code == 200
            assert removed.json()["scene"]["cameras"] == []



@pytest.mark.asyncio
async def test_geometry_diagnostic_job_is_traceable_and_non_mutating(settings):
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
                json={"name": "Quality project"},
            )
            project_id = project.json()["id"]

            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.quality",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "surface.wall.main",
                            "kind": "wall",
                            "locks": {"geometry": True, "transform": True},
                        }
                    ],
                    "cameras": [
                        {
                            "id": "camera.main",
                            "width_px": 100,
                            "height_px": 100,
                            "intrinsics": {"fx": 80, "fy": 80, "cx": 50, "cy": 50},
                            "transform": {
                                "translation_mm": [0, -1000, 1000],
                                "rotation_deg": [90, 0, 0],
                            },
                        }
                    ],
                },
            )
            revision_id = scene.json()["revision_id"]

            asset_ids = []
            for name in ("reference.png", "generated.png"):
                upload = await client.post(
                    f"/api/v1/projects/{project_id}/assets",
                    headers=headers,
                    data={"role": "derived"},
                    files={"file": (name, png_bytes(), "image/png")},
                )
                assert upload.status_code == 201
                asset_ids.append(upload.json()["id"])

            queued = await client.post(
                f"/api/v1/projects/{project_id}/geometry-diagnostics",
                headers=headers,
                json={
                    "scene_revision_id": revision_id,
                    "camera_id": "camera.main",
                    "reference_asset_id": asset_ids[0],
                    "generated_asset_id": asset_ids[1],
                    "advisory_threshold": 0.8,
                },
            )
            assert queued.status_code == 201
            job_id = queued.json()["id"]

            worker_headers = {"Authorization": "Bearer worker-secret"}
            await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "quality-worker",
                    "capabilities": ["geometry_quality"],
                },
            )
            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "quality-worker"},
            )
            lease = claim.json()
            diagnostic_id = lease["payload"]["diagnostic_id"]
            assert set(lease["download_urls"]) == set(asset_ids)

            completed = await client.post(
                f"/api/v1/workers/jobs/{job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "quality-worker",
                    "lease_id": lease["lease_id"],
                    "result": {
                        "geometry_diagnostic": {
                            "schema_version": "0.1.0",
                            "diagnostic_id": diagnostic_id,
                            "scene_revision_id": revision_id,
                            "camera_id": "camera.main",
                            "reference_asset_id": asset_ids[0],
                            "generated_asset_id": asset_ids[1],
                            "score": 0.55,
                            "edge_precision": 0.60,
                            "edge_recall": 0.51,
                            "edge_f1": 0.55,
                            "reference_edge_pixels": 100,
                            "generated_edge_pixels": 110,
                            "tolerance_px": 2,
                            "advisory_threshold": 0.8,
                            "advisory_pass": False,
                            "limitations": ["advisory only"],
                        }
                    },
                },
            )
            assert completed.status_code == 200
            assert completed.json()["result"]["diagnostic_id"] == diagnostic_id

            diagnostics = await client.get(
                f"/api/v1/projects/{project_id}/geometry-diagnostics"
            )
            assert diagnostics.status_code == 200
            assert diagnostics.json()[0]["diagnostic"]["score"] == 0.55
            assert diagnostics.json()[0]["diagnostic"]["scene_revision_id"] == revision_id

            latest = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert latest.status_code == 200
            assert latest.json()["revision_id"] == revision_id
            assert latest.json()["scene"]["entities"][0]["locks"]["geometry"] is True



@pytest.mark.asyncio
async def test_multi_command_edit_generation_undo_and_rerender(settings):
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
                json={"name": "Edit loop"},
            )
            project_id = project.json()["id"]

            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.edit-loop",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "surface.wall.living.north",
                            "kind": "wall",
                            "locks": {"geometry": True, "transform": True},
                        },
                        {
                            "id": "object.sofa.main",
                            "kind": "furniture",
                            "material_ref": "material.fabric.gray",
                            "locks": {},
                        },
                        {
                            "id": "object.coffee_table.main",
                            "kind": "furniture",
                            "locks": {},
                        },
                    ],
                    "cameras": [
                        {
                            "id": "camera.main",
                            "width_px": 640,
                            "height_px": 400,
                            "intrinsics": {
                                "fx": 500,
                                "fy": 500,
                                "cx": 320,
                                "cy": 200,
                            },
                            "transform": {
                                "translation_mm": [0, -4000, 1600],
                                "rotation_deg": [90, 0, 0],
                            },
                        }
                    ],
                },
            )
            assert scene.status_code == 201
            base_revision_id = scene.json()["revision_id"]

            instruction = await client.post(
                f"/api/v1/projects/{project_id}/design/instructions",
                headers=headers,
                json={
                    "text": "make sofa beige, lighten wood, remove table",
                    "idempotency_key": "edit-1",
                },
            )
            assert instruction.status_code == 201
            llm_job_id = instruction.json()["id"]

            worker_headers = {"Authorization": "Bearer worker-secret"}
            register = await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "edit-worker",
                    "capabilities": ["llm", "image_generation"],
                    "models": ["fake"],
                },
            )
            assert register.status_code == 200

            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "edit-worker"},
            )
            assert claim.status_code == 200
            llm_lease = claim.json()
            assert llm_lease["job_id"] == llm_job_id

            completed = await client.post(
                f"/api/v1/workers/jobs/{llm_job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "edit-worker",
                    "lease_id": llm_lease["lease_id"],
                    "result": {
                        "tool_calls": [
                            {
                                "name": "set_color",
                                "arguments": {
                                    "target_id": "object.sofa.main",
                                    "color": "#D7C4AB",
                                },
                            },
                            {
                                "name": "set_material",
                                "arguments": {
                                    "target_id": "object.sofa.main",
                                    "material_ref": "material.wood.light-oak",
                                },
                            },
                            {
                                "name": "remove_object",
                                "arguments": {
                                    "target_id": "object.coffee_table.main",
                                },
                            },
                        ]
                    },
                },
            )
            assert completed.status_code == 200
            llm_result = completed.json()["result"]
            assert len(llm_result["commands"]) == 3
            assert len(llm_result["applied_revision_ids"]) == 3
            assert set(llm_result["affected_entity_ids"]) == {
                "object.sofa.main",
                "object.coffee_table.main",
            }
            assert len(llm_result["generation_job_ids"]) == 1
            final_revision_id = llm_result["final_revision_id"]

            current = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert current.status_code == 200
            assert current.json()["revision_id"] == final_revision_id
            entities = {
                item["id"]: item
                for item in current.json()["scene"]["entities"]
            }
            assert entities["object.sofa.main"]["metadata"]["color"] == "#D7C4AB"
            assert (
                entities["object.sofa.main"]["material_ref"]
                == "material.wood.light-oak"
            )
            assert "object.coffee_table.main" not in entities
            assert entities["surface.wall.living.north"]["locks"]["geometry"] is True

            generation_claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "edit-worker"},
            )
            assert generation_claim.status_code == 200
            generation_lease = generation_claim.json()
            assert generation_lease["job_type"] == "image.generate"
            generation_payload = generation_lease["payload"]
            assert generation_payload["design_revision_id"] == final_revision_id
            assert generation_payload["regeneration_scope"] == "targeted"
            assert set(generation_payload["affected_entity_ids"]) == {
                "object.sofa.main",
                "object.coffee_table.main",
            }
            assert "surface.wall.living.north" in generation_payload[
                "generation"
            ]["structured_conditioning"]["protected_entity_ids"]

            output = await client.post(
                f"/api/v1/workers/jobs/{generation_lease['job_id']}/outputs",
                headers=worker_headers,
                data={
                    "worker_id": "edit-worker",
                    "lease_id": generation_lease["lease_id"],
                    "semantic_name": "image",
                },
                files={"file": ("generated.png", png_bytes(), "image/png")},
            )
            assert output.status_code == 201
            output_asset_id = output.json()["id"]
            generation_context = generation_payload["generation"]
            workflow = generation_payload["workflow_manifest"]

            generation_complete = await client.post(
                f"/api/v1/workers/jobs/{generation_lease['job_id']}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "edit-worker",
                    "lease_id": generation_lease["lease_id"],
                    "result": {
                        "generation_manifest": {
                            "schema_version": "0.1.0",
                            "generation_id": generation_context["generation_id"],
                            "scene_revision_id": final_revision_id,
                            "design_revision_id": final_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {
                                "id": workflow["id"],
                                "version": workflow["version"],
                            },
                            "model_profile": workflow["model_profile"],
                            "seed": 0,
                            "input_asset_ids": [],
                            "output_asset_ids": [output_asset_id],
                            "structured_conditioning": generation_context[
                                "structured_conditioning"
                            ],
                        },
                        "output_asset_ids": [output_asset_id],
                    },
                },
            )
            assert generation_complete.status_code == 200
            generation_id = generation_complete.json()["result"]["generation_id"]

            manifests = await client.get(
                f"/api/v1/projects/{project_id}/generations"
            )
            assert manifests.status_code == 200
            assert manifests.json()[0]["id"] == generation_id
            assert (
                manifests.json()[0]["design_revision_id"]
                == final_revision_id
            )
            assert manifests.json()[0]["manifest"]["output_asset_ids"] == [
                output_asset_id
            ]

            undo = await client.post(
                f"/api/v1/projects/{project_id}/scene/revert",
                headers=headers,
                json={
                    "expected_base_revision_id": final_revision_id,
                    "target_revision_id": base_revision_id,
                },
            )
            assert undo.status_code == 200
            restored_revision_id = undo.json()["revision_id"]
            restored_entities = {
                item["id"]: item for item in undo.json()["scene"]["entities"]
            }
            assert "object.coffee_table.main" in restored_entities
            assert restored_entities["object.sofa.main"]["material_ref"] == "material.fabric.gray"

            rerender = await client.post(
                f"/api/v1/projects/{project_id}/generations",
                headers=headers,
                json={
                    "design_revision_id": restored_revision_id,
                    "camera_id": "camera.main",
                    "prompt": "re-render restored design",
                },
            )
            assert rerender.status_code == 201

            rerender_claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "edit-worker"},
            )
            assert rerender_claim.status_code == 200
            assert rerender_claim.json()["payload"]["design_revision_id"] == restored_revision_id
            assert rerender_claim.json()["payload"]["regeneration_scope"] == "full"



@pytest.mark.asyncio
async def test_reference_object_replacement_flow(settings):
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
                json={"name": "Replacement"},
            )
            project_id = project.json()["id"]

            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.replacement",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "surface.wall.main",
                            "kind": "wall",
                            "transform": {
                                "translation_mm": [0, 2000, 1400],
                                "rotation_deg": [0, 0, 0],
                                "scale": [1, 1, 1],
                            },
                            "geometry": {"dimensions_mm": [5000, 120, 2800]},
                            "locks": {"geometry": True, "transform": True},
                        },
                        {
                            "id": "object.sofa.main",
                            "kind": "furniture",
                            "transform": {
                                "translation_mm": [0, 0, 900],
                                "rotation_deg": [0, 0, 0],
                                "scale": [1, 1, 1],
                            },
                            "geometry": {"dimensions_mm": [2200, 900, 900]},
                            "locks": {},
                        },
                    ],
                    "cameras": [
                        {
                            "id": "camera.main",
                            "width_px": 1000,
                            "height_px": 800,
                            "intrinsics": {
                                "fx": 800,
                                "fy": 800,
                                "cx": 500,
                                "cy": 400,
                            },
                            "transform": {
                                "translation_mm": [0, -5000, 1500],
                                "rotation_deg": [90, 0, 0],
                            },
                        }
                    ],
                },
            )
            assert scene.status_code == 201
            base_revision_id = scene.json()["revision_id"]
            original_camera = scene.json()["scene"]["cameras"][0]

            reference_bytes = png_bytes()
            reference = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("chair-reference.png", reference_bytes, "image/png")},
            )
            assert reference.status_code == 201
            reference_id = reference.json()["id"]
            reference_sha = reference.json()["sha256"]

            # Seed a prior generation on camera.main (the base image a real
            # replacement edits): a 64x48 generated PNG + its manifest row.
            base_buffer = BytesIO()
            Image.new("RGB", (64, 48), "lightgray").save(base_buffer, format="PNG")
            base_bytes = base_buffer.getvalue()
            base_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("room-base.png", base_bytes, "image/png")},
            )
            assert base_upload.status_code == 201
            base_asset_id = base_upload.json()["id"]
            async with app.state.session_factory() as db:
                db.add(
                    GenerationManifestRow(
                        id=str(uuid4()),
                        project_id=project_id,
                        job_id=str(uuid4()),
                        scene_revision_id=base_revision_id,
                        design_revision_id=base_revision_id,
                        camera_id="camera.main",
                        manifest_json={
                            "generation_id": "gen-base",
                            "scene_revision_id": base_revision_id,
                            "design_revision_id": base_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {
                                "id": "flux-redesign-v0",
                                "version": "0.2.0",
                            },
                            "model_profile": "flux-dev-family",
                            "seed": 0,
                            "input_asset_ids": [],
                            "output_asset_ids": [base_asset_id],
                            "structured_conditioning": {},
                        },
                    )
                )
                await db.commit()

            replacement = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": reference_id,
                    "camera_id": "camera.main",
                    "prompt": "replace the sofa with the reference furniture",
                },
            )
            assert replacement.status_code == 201, replacement.json()
            body = replacement.json()
            replacement_revision_id = body["revision_id"]
            assert replacement_revision_id != base_revision_id
            assert body["command"]["operation"] == "replace_object_from_reference"
            assert body["command"]["target_id"] == "object.sofa.main"
            assert body["command"]["reference_asset_ids"] == [reference_id]
            assert body["affected_region"]["type"] == "projected_bbox"
            assert body["affected_region"]["target_entity_id"] == "object.sofa.main"
            assert body["base_asset_id"] == base_asset_id
            assert body["mask_asset_id"]

            next_scene = body["scene"]
            entities = {item["id"]: item for item in next_scene["entities"]}
            assert (
                entities["object.sofa.main"]["metadata"][
                    "replacement_reference_asset_id"
                ]
                == reference_id
            )
            assert entities["surface.wall.main"]["locks"]["geometry"] is True
            assert next_scene["cameras"][0] == original_camera

            worker_headers = {"Authorization": "Bearer worker-secret"}
            registered = await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "edit-worker",
                    "capabilities": ["image_edit"],
                    "models": ["fake"],
                },
            )
            assert registered.status_code == 200

            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "edit-worker"},
            )
            assert claim.status_code == 200
            lease = claim.json()
            assert lease["job_type"] == "image.edit"
            payload = lease["payload"]
            assert payload["design_revision_id"] == replacement_revision_id
            assert payload["input_asset_ids"] == [
                base_asset_id,
                reference_id,
                body["mask_asset_id"],
            ]
            assert payload["asset_roles"] == {
                "base_image": base_asset_id,
                "reference_image": reference_id,
                "mask_image": body["mask_asset_id"],
                # v0.3.0: the same reference asset feeds the IPAdapterFlux
                # identity path as the control image
                "control_image": reference_id,
            }
            assert payload["replacement"]["target_entity_id"] == "object.sofa.main"
            assert payload["replacement"]["reference_asset_id"] == reference_id
            assert payload["replacement"]["affected_region"] == body["affected_region"]
            assert "surface.wall.main" in payload["generation"][
                "structured_conditioning"
            ]["protected_entity_ids"]

            output = await client.post(
                f"/api/v1/workers/jobs/{lease['job_id']}/outputs",
                headers=worker_headers,
                data={
                    "worker_id": "edit-worker",
                    "lease_id": lease["lease_id"],
                    "semantic_name": "image",
                },
                files={"file": ("replacement.png", png_bytes(), "image/png")},
            )
            assert output.status_code == 201
            output_asset_id = output.json()["id"]
            generation = payload["generation"]
            workflow = payload["workflow_manifest"]

            completed = await client.post(
                f"/api/v1/workers/jobs/{lease['job_id']}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "edit-worker",
                    "lease_id": lease["lease_id"],
                    "result": {
                        "generation_manifest": {
                            "schema_version": "0.1.0",
                            "generation_id": generation["generation_id"],
                            "scene_revision_id": replacement_revision_id,
                            "design_revision_id": replacement_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {
                                "id": workflow["id"],
                                "version": workflow["version"],
                            },
                            "model_profile": workflow["model_profile"],
                            "seed": 0,
                            "input_asset_ids": [reference_id],
                            "output_asset_ids": [output_asset_id],
                            "structured_conditioning": generation[
                                "structured_conditioning"
                            ],
                        },
                        "output_asset_ids": [output_asset_id],
                    },
                },
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "succeeded"
            generation_id = completed.json()["result"]["generation_id"]

            manifests = await client.get(
                f"/api/v1/projects/{project_id}/generations"
            )
            assert manifests.status_code == 200
            stored = next(item for item in manifests.json() if item["id"] == generation_id)
            assert stored["design_revision_id"] == replacement_revision_id
            assert stored["manifest"]["input_asset_ids"] == [reference_id]
            assert stored["manifest"]["output_asset_ids"] == [output_asset_id]
            assert stored["manifest"]["structured_conditioning"][
                "affected_region"
            ] == body["affected_region"]

            reference_after = await client.get(
                f"/api/v1/assets/{reference_id}"
            )
            assert reference_after.status_code == 200
            assert reference_after.content == reference_bytes

            assets = await client.get(
                f"/api/v1/projects/{project_id}/assets"
            )
            reference_row = next(
                item for item in assets.json() if item["id"] == reference_id
            )
            assert reference_row["sha256"] == reference_sha
            assert reference_row["role"] == "reference"
            assert reference_row["provenance"] == "user"


@pytest.mark.asyncio
async def test_reference_object_replacement_with_mask_region(settings, monkeypatch):
    # Never touch the real rembg model/network: force the graceful fallback.
    from stroy.editing import segmentation

    monkeypatch.setattr(segmentation, "segment_subject", lambda *a, **k: None)
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
                json={"name": "Replacement mask region"},
            )
            project_id = project.json()["id"]

            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.replacement",
                    "project_id": project_id,
                    "entities": [
                        {
                            "id": "surface.wall.main",
                            "kind": "wall",
                            "transform": {
                                "translation_mm": [0, 2000, 1400],
                                "rotation_deg": [0, 0, 0],
                                "scale": [1, 1, 1],
                            },
                            "geometry": {"dimensions_mm": [5000, 120, 2800]},
                            "locks": {"geometry": True, "transform": True},
                        },
                        {
                            "id": "object.sofa.main",
                            "kind": "furniture",
                            "transform": {
                                "translation_mm": [0, 0, 900],
                                "rotation_deg": [0, 0, 0],
                                "scale": [1, 1, 1],
                            },
                            "geometry": {"dimensions_mm": [2200, 900, 900]},
                            "locks": {},
                        },
                    ],
                    "cameras": [
                        {
                            "id": "camera.main",
                            "width_px": 1000,
                            "height_px": 800,
                            "intrinsics": {
                                "fx": 800,
                                "fy": 800,
                                "cx": 500,
                                "cy": 400,
                            },
                            "transform": {
                                "translation_mm": [0, -5000, 1500],
                                "rotation_deg": [90, 0, 0],
                            },
                        }
                    ],
                },
            )
            assert scene.status_code == 201
            base_revision_id = scene.json()["revision_id"]

            reference = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("chair-reference.png", png_bytes(), "image/png")},
            )
            assert reference.status_code == 201
            reference_id = reference.json()["id"]

            # Seed a prior generation on camera.main. The base PNG is
            # 1024x1024 so its metadata carries width_px/height_px large
            # enough for the client mask_region (in base-image pixels) to
            # be rendered verbatim.
            base_buffer = BytesIO()
            Image.new("RGB", (1024, 1024), "lightgray").save(
                base_buffer, format="PNG"
            )
            base_bytes = base_buffer.getvalue()
            base_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("room-base.png", base_bytes, "image/png")},
            )
            assert base_upload.status_code == 201
            base_asset_id = base_upload.json()["id"]
            async with app.state.session_factory() as db:
                db.add(
                    GenerationManifestRow(
                        id=str(uuid4()),
                        project_id=project_id,
                        job_id=str(uuid4()),
                        scene_revision_id=base_revision_id,
                        design_revision_id=base_revision_id,
                        camera_id="camera.main",
                        manifest_json={
                            "generation_id": "gen-base",
                            "scene_revision_id": base_revision_id,
                            "design_revision_id": base_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {
                                "id": "flux-redesign-v0",
                                "version": "0.2.0",
                            },
                            "model_profile": "flux-dev-family",
                            "seed": 0,
                            "input_asset_ids": [],
                            "output_asset_ids": [base_asset_id],
                            "structured_conditioning": {},
                        },
                    )
                )
                await db.commit()

            replacement = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": reference_id,
                    "camera_id": "camera.main",
                    "prompt": "replace the sofa with the reference furniture",
                    "mask_region": [334, 578, 654, 884],
                },
            )
            assert replacement.status_code == 201, replacement.json()
            body = replacement.json()
            # mask_region bypasses the camera projection: the client box is
            # used verbatim in base-image pixel space (client_override branch).
            assert body["affected_region"]["type"] == "client_override"
            assert body["affected_region"]["bbox_px"] == [334, 578, 654, 884]
            assert body["mask_asset_id"]

            # An explicit silhouette shape is accepted by the API. rembg is not
            # installed in the test env, so the mask must gracefully fall back
            # to the rectangle rendering and still return 201.
            silhouette = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": body["revision_id"],
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": reference_id,
                    "camera_id": "camera.main",
                    "prompt": "replace the sofa with the reference furniture",
                    "mask_region": [334, 578, 654, 884],
                    "shape": "silhouette",
                },
            )
            assert silhouette.status_code == 201, silhouette.json()
            assert silhouette.json()["mask_asset_id"]

            # A failing object store on the base-image fetch must still return
            # 201 with the rectangle mask (graceful degradation).
            original_get = app.state.object_store.get_bytes
            calls = {"count": 0}

            async def flaky_get(key):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise RuntimeError("simulated object store outage")
                return await original_get(key)

            monkeypatch.setattr(app.state.object_store, "get_bytes", flaky_get)
            store_down = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": silhouette.json()["revision_id"],
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": reference_id,
                    "camera_id": "camera.main",
                    "prompt": "replace the sofa with the reference furniture",
                    "mask_region": [334, 578, 654, 884],
                    "shape": "silhouette",
                },
            )
            assert store_down.status_code == 201, store_down.json()
            mask_id = store_down.json()["mask_asset_id"]

            expected_region = ReplacementRegion(
                type="client_override",
                target_entity_id="object.sofa.main",
                camera_id="camera.main",
                bbox_px=(334, 578, 654, 884),
                feather_px=24,
                source="client_mask_region",
            )
            expected_mask = render_replacement_mask(expected_region, 1024, 1024, 1024, 1024)
            downloaded = await client.get(
                f"/api/v1/assets/{mask_id}", headers=headers
            )
            assert downloaded.status_code == 200
            assert downloaded.content == expected_mask



@pytest.mark.asyncio
async def test_worker_token_rotation_and_runtime_compatibility(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'rotation.db'}",
        auto_create_schema=True,
        auth_username="owner",
        auth_password_hash=PasswordHasher().hash("secret"),
        session_cookie_secure=False,
        trusted_hosts="test",
        worker_token="",
        worker_token_hashes=",".join(
            [sha256_text("worker-old"), sha256_text("worker-new")]
        ),
        storage_backend="memory",
    )
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
                json={"name": "Worker compatibility"},
            )
            project_id = project.json()["id"]

            job = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=owner_headers,
                json={
                    "job_type": "image.generate",
                    "payload": {
                        "required_models": ["flux1-schnell"],
                        "required_runtimes": {"comfyui": {}},
                    },
                    "required_capabilities": ["image_generation"],
                },
            )
            assert job.status_code == 201

            old_headers = {"Authorization": "Bearer worker-old"}
            new_headers = {"Authorization": "Bearer worker-new"}
            assert (
                await client.post(
                    "/api/v1/workers/register",
                    headers=old_headers,
                    json={
                        "worker_id": "worker-old-profile",
                        "capabilities": ["image_generation"],
                        "models": ["flux-dev-family"],
                        "runtimes": {"comfyui": {"status": "ready"}},
                    },
                )
            ).status_code == 200
            incompatible = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=old_headers,
                json={"worker_id": "worker-old-profile"},
            )
            assert incompatible.status_code == 204

            assert (
                await client.post(
                    "/api/v1/workers/register",
                    headers=new_headers,
                    json={
                        "worker_id": "worker-compatible",
                        "capabilities": ["image_generation"],
                        "models": ["flux1-schnell"],
                        "runtimes": {"comfyui": {"status": "ready"}},
                    },
                )
            ).status_code == 200
            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=new_headers,
                json={"worker_id": "worker-compatible"},
            )
            assert claim.status_code == 200
            lease = claim.json()

            released = await client.post(
                f"/api/v1/workers/jobs/{lease['job_id']}/release",
                headers=new_headers,
                json={
                    "worker_id": "worker-compatible",
                    "lease_id": lease["lease_id"],
                },
            )
            assert released.status_code == 200
            assert released.json()["status"] == "waiting_for_worker"
            assert released.json()["leased_to"] is None


@pytest.mark.asyncio
async def test_worker_lease_status_reports_owner_cancellation(settings):
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
                json={"name": "Cancellation status"},
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
            worker_headers = {"Authorization": "Bearer worker-secret"}
            await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "worker-cancel-status",
                    "capabilities": ["blender_render"],
                },
            )
            claim = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-cancel-status"},
            )
            lease = claim.json()
            await client.post(
                f"/api/v1/jobs/{job.json()['id']}/cancel",
                headers=owner_headers,
            )
            status_response = await client.post(
                f"/api/v1/workers/jobs/{job.json()['id']}/lease-status",
                headers=worker_headers,
                json={
                    "worker_id": "worker-cancel-status",
                    "lease_id": lease["lease_id"],
                },
            )
            assert status_response.status_code == 200
            assert status_response.json() == {
                "status": "cancelled",
                "lease_valid": False,
            }



@pytest.mark.asyncio
async def test_worker_release_and_reclaim_simulates_restart(settings):
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
                json={"name": "Worker restart"},
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
            assert job.status_code == 201
            job_id = job.json()["id"]
            assert job.json()["status"] == "waiting_for_worker"

            worker_headers = {"Authorization": "Bearer worker-secret"}
            registered = await client.post(
                "/api/v1/workers/register",
                headers=worker_headers,
                json={
                    "worker_id": "worker-restart",
                    "capabilities": ["blender_render"],
                    "models": [],
                    "runtimes": {"blender": {"status": "ready"}},
                },
            )
            assert registered.status_code == 200

            first = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-restart"},
            )
            assert first.status_code == 200
            first_lease = first.json()
            assert first_lease["attempt"] == 1

            released = await client.post(
                f"/api/v1/workers/jobs/{job_id}/release",
                headers=worker_headers,
                json={
                    "worker_id": "worker-restart",
                    "lease_id": first_lease["lease_id"],
                },
            )
            assert released.status_code == 200
            assert released.json()["status"] == "waiting_for_worker"

            second = await client.post(
                "/api/v1/workers/jobs/claim",
                headers=worker_headers,
                json={"worker_id": "worker-restart"},
            )
            assert second.status_code == 200
            second_lease = second.json()
            assert second_lease["attempt"] == 2
            assert second_lease["lease_id"] != first_lease["lease_id"]

            stale = await client.post(
                f"/api/v1/workers/jobs/{job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-restart",
                    "lease_id": first_lease["lease_id"],
                    "result": {},
                },
            )
            assert stale.status_code == 409


@pytest.mark.asyncio
async def test_ui_image_job_gets_default_generation_context(settings):
    """UI-created image jobs must carry a generation context the worker accepts."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            project_response = await client.post(
                "/api/v1/projects", json={"name": "UI gen"}, headers=headers
            )
            assert project_response.status_code == 201
            project_id = project_response.json()["id"]

            job_response = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=headers,
                json={
                    "job_type": "image.generate",
                    "payload": {"scene_revision_id": "rev-ui-1"},
                    "required_capabilities": ["image_generation"],
                },
            )
            assert job_response.status_code == 201

            async with app.state.session_factory() as db:
                job_row = (
                    await db.execute(
                        select(JobRow).where(JobRow.project_id == project_id)
                    )
                ).scalar_one()
                generation = job_row.payload["generation"]
                assert generation["scene_revision_id"] == "rev-ui-1"
                assert generation["design_revision_id"] == "rev-ui-1"
                assert generation["camera_id"] == "default"
                assert generation["generation_id"]
                assert (
                    job_row.payload["workflow_manifest"]["id"] == "flux-redesign-v0"
                )

            provided = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=headers,
                json={
                    "job_type": "image.generate",
                    "payload": {
                        "generation": {
                            "generation_id": "gen-fixed",
                            "scene_revision_id": "rev-ui-1",
                            "design_revision_id": "rev-ui-1",
                            "camera_id": "camera.main",
                        }
                    },
                    "required_capabilities": ["image_generation"],
                    "idempotency_key": "provided-1",
                },
            )
            assert provided.status_code == 201

            async with app.state.session_factory() as db:
                row = (
                    await db.execute(
                        select(JobRow).where(JobRow.idempotency_key == "provided-1")
                    )
                ).scalar_one()
                assert row.payload["generation"]["generation_id"] == "gen-fixed"
                assert "workflow_manifest" not in row.payload


def test_style_analyze_request_allows_missing_source_text():
    """UI fires style analysis without a brief; the API must accept it."""
    from stroy.api.routes import StyleAnalyzeRequest

    request = StyleAnalyzeRequest.model_validate(
        {"reference_asset_ids": ["a-1", "a-2", "a-3"]}
    )
    assert request.source_text is None
    request = StyleAnalyzeRequest.model_validate(
        {"reference_asset_ids": ["a-1", "a-2", "a-3"], "source_text": ""}
    )
    assert request.source_text == ""


@pytest.mark.asyncio
async def test_mask_only_replacement_without_scene_entity(settings, monkeypatch):
    """Photo-first replacement: a client mask_region alone is enough; no
    canonical entity/camera lookup, no DesignCommandRow, job queued with a
    client_override region and a null entity in the payload."""
    from stroy.editing import segmentation

    monkeypatch.setattr(segmentation, "segment_subject", lambda *a, **k: None)
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Photo-first"}
            )
            project_id = project.json()["id"]

            # Scene exists (lineage anchor) but the request references NO entity.
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.photo",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            assert scene.status_code == 201
            base_revision_id = scene.json()["revision_id"]

            reference = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("ref.png", png_bytes(), "image/png")},
            )
            assert reference.status_code == 201
            reference_id = reference.json()["id"]

            base_buffer = BytesIO()
            Image.new("RGB", (1024, 1024), "lightgray").save(
                base_buffer, format="PNG"
            )
            base_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("room.png", base_buffer.getvalue(), "image/png")},
            )
            assert base_upload.status_code == 201
            base_asset_id = base_upload.json()["id"]
            async with app.state.session_factory() as db:
                db.add(
                    GenerationManifestRow(
                        id=str(uuid4()),
                        project_id=project_id,
                        job_id=str(uuid4()),
                        scene_revision_id=base_revision_id,
                        design_revision_id=base_revision_id,
                        camera_id="camera.main",
                        manifest_json={
                            "generation_id": "gen-base",
                            "scene_revision_id": base_revision_id,
                            "design_revision_id": base_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {"id": "flux-redesign-v0", "version": "0.2.0"},
                            "model_profile": "flux-dev-family",
                            "seed": 0,
                            "input_asset_ids": [],
                            "output_asset_ids": [base_asset_id],
                            "structured_conditioning": {},
                        },
                    )
                )
                await db.commit()

            replacement = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "reference_asset_id": reference_id,
                    "base_asset_id": base_asset_id,
                    "mask_region": [100, 100, 400, 400],
                    "prompt": "remove the sofa",
                },
            )
            assert replacement.status_code == 201, replacement.json()
            body = replacement.json()
            assert body["command"] is None
            assert body["affected_region"]["type"] == "client_override"
            assert body["affected_region"]["target_entity_id"] is None
            assert body["affected_region"]["camera_id"] is None
            assert body["mask_asset_id"]
            assert body["base_asset_id"] == base_asset_id

            async with app.state.session_factory() as db:
                job_row = (
                    await db.execute(
                        select(JobRow).where(JobRow.job_type == "image.edit")
                    )
                ).scalar_one()
                payload = job_row.payload
                assert payload["replacement"]["target_entity_id"] is None
                assert payload["affected_entity_ids"] == []
                assert payload["generation"]["camera_id"] == "default"
                assert payload["asset_roles"]["base_image"] == base_asset_id
                assert payload["asset_roles"]["mask_image"] == body["mask_asset_id"]
                # no DesignCommandRow was written for the no-scene path
                commands = (
                    await db.execute(select(DesignCommandRow))
                ).scalars().all()
                assert commands == []


@pytest.mark.asyncio
async def test_replacement_without_reference_binds_black_placeholder(settings, monkeypatch):
    """Remove/Restyle: mask_region + NO reference_asset_id succeeds without a
    client-uploaded placeholder. The server binds a synthetic black reference
    slot and silently forces ipa_weight to 0.0 (never 422)."""
    from stroy.editing import segmentation

    monkeypatch.setattr(segmentation, "segment_subject", lambda *a, **k: None)
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}

            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "No ref edit"}
            )
            project_id = project.json()["id"]

            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.noref",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            assert scene.status_code == 201
            base_revision_id = scene.json()["revision_id"]

            # No reference asset is uploaded by the client at all.
            base_buffer = BytesIO()
            Image.new("RGB", (1024, 1024), "lightgray").save(
                base_buffer, format="PNG"
            )
            base_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("room.png", base_buffer.getvalue(), "image/png")},
            )
            assert base_upload.status_code == 201
            base_asset_id = base_upload.json()["id"]

            replacement = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "base_asset_id": base_asset_id,
                    "mask_region": [100, 100, 400, 400],
                    "prompt": "remove the sofa",
                    # an explicit weight > 0 must be silently forced to 0.0
                    "ipa_weight": 1.5,
                },
            )
            assert replacement.status_code == 201, replacement.json()
            body = replacement.json()
            assert body["command"] is None
            assert body["affected_region"]["type"] == "client_override"
            assert body["ipa_weight"] == 0.0
            synthetic_id = body["reference_asset_id"]
            assert synthetic_id

            async with app.state.session_factory() as db:
                ref_row = await db.get(AssetRow, synthetic_id)
                assert ref_row is not None
                assert ref_row.project_id == project_id
                assert ref_row.role == "reference"
                assert ref_row.media_type == "image/png"
                assert ref_row.metadata_json["source"] == (
                    "replacement_black_reference"
                )

                job_row = (
                    await db.execute(
                        select(JobRow).where(JobRow.job_type == "image.edit")
                    )
                ).scalar_one()
                payload = job_row.payload
                assert payload["ipa_weight"] == 0.0
                # the real reference is reported as absent, but the synthetic
                # asset stays bound to the slot
                assert payload["replacement"]["reference_asset_id"] is None
                assert (
                    payload["generation"]["structured_conditioning"][
                        "replacement_reference_asset_id"
                    ]
                    is None
                )
                assert payload["asset_roles"]["reference_image"] == synthetic_id
                assert payload["asset_roles"]["control_image"] == synthetic_id
                assert "noreference" in job_row.idempotency_key

            downloaded = await client.get(
                f"/api/v1/assets/{synthetic_id}", headers=headers
            )
            assert downloaded.status_code == 200
            assert Image.open(BytesIO(downloaded.content)).size == (1, 1)


@pytest.mark.asyncio
async def test_replacement_without_mask_or_entity_is_422(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "No mask"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.nomask",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]
            reference = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("ref.png", png_bytes(), "image/png")},
            )
            reference_id = reference.json()["id"]

            response = await client.post(
                f"/api/v1/projects/{project_id}/replacements",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "reference_asset_id": reference_id,
                    "prompt": "replace something",
                },
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_reference_redesign_flow(settings):
    """Whole-room redesign toward a reference interior: 201, job queued with
    the new manifest, strength mapped to KSampler.denoise, asset_roles set."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Redesign"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.redesign",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]

            reference = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("interior.png", png_bytes(), "image/png")},
            )
            reference_id = reference.json()["id"]

            base_buffer = BytesIO()
            Image.new("RGB", (1024, 1024), "lightgray").save(
                base_buffer, format="PNG"
            )
            base_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("room.png", base_buffer.getvalue(), "image/png")},
            )
            base_asset_id = base_upload.json()["id"]
            async with app.state.session_factory() as db:
                db.add(
                    GenerationManifestRow(
                        id=str(uuid4()),
                        project_id=project_id,
                        job_id=str(uuid4()),
                        scene_revision_id=base_revision_id,
                        design_revision_id=base_revision_id,
                        camera_id="camera.main",
                        manifest_json={
                            "generation_id": "gen-base",
                            "scene_revision_id": base_revision_id,
                            "design_revision_id": base_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {"id": "flux-redesign-v0", "version": "0.2.0"},
                            "model_profile": "flux-dev-family",
                            "seed": 0,
                            "input_asset_ids": [],
                            "output_asset_ids": [base_asset_id],
                            "structured_conditioning": {},
                        },
                    )
                )
                await db.commit()

            redesign = await client.post(
                f"/api/v1/projects/{project_id}/redesigns",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "reference_asset_id": reference_id,
                    "prompt": "warm scandinavian living room",
                    "strength": 0.7,
                    "seed": 42,
                },
            )
            assert redesign.status_code == 201, redesign.json()
            body = redesign.json()
            assert body["base_asset_id"] == base_asset_id
            assert body["reference_asset_id"] == reference_id

            async with app.state.session_factory() as db:
                job_row = (
                    await db.execute(
                        select(JobRow).where(JobRow.job_type == "image.edit")
                    )
                ).scalar_one()
                payload = job_row.payload
                assert payload["workflow_manifest"]["id"] == (
                    "image-redesign-reference-v1"
                )
                assert payload["inputs"]["strength"] == 0.7
                assert payload["inputs"]["seed"] == 42
                assert payload["asset_roles"] == {
                    "base_image": base_asset_id,
                    "reference_image": reference_id,
                }
                assert payload["ipa_weight"] == 0.85
                graph = WorkflowManifest.model_validate(
                    payload["workflow_manifest"]
                ).materialize(
                    {
                        **payload["inputs"],
                        "base_image": "base.png",
                        "reference_image": "ref.png",
                    }
                )
                assert graph["13"]["inputs"]["denoise"] == 0.7


@pytest.mark.asyncio
async def test_reference_redesign_without_reference_uses_black_placeholder(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Redesign none"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.redesign.none",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]
            base_buffer = BytesIO()
            Image.new("RGB", (1024, 1024), "lightgray").save(
                base_buffer, format="PNG"
            )
            base_upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("room.png", base_buffer.getvalue(), "image/png")},
            )
            base_asset_id = base_upload.json()["id"]
            async with app.state.session_factory() as db:
                db.add(
                    GenerationManifestRow(
                        id=str(uuid4()),
                        project_id=project_id,
                        job_id=str(uuid4()),
                        scene_revision_id=base_revision_id,
                        design_revision_id=base_revision_id,
                        camera_id="camera.main",
                        manifest_json={
                            "generation_id": "gen-base",
                            "scene_revision_id": base_revision_id,
                            "design_revision_id": base_revision_id,
                            "camera_id": "camera.main",
                            "workflow": {"id": "flux-redesign-v0", "version": "0.2.0"},
                            "model_profile": "flux-dev-family",
                            "seed": 0,
                            "input_asset_ids": [],
                            "output_asset_ids": [base_asset_id],
                            "structured_conditioning": {},
                        },
                    )
                )
                await db.commit()

            redesign = await client.post(
                f"/api/v1/projects/{project_id}/redesigns",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "prompt": "minimalist restyle",
                },
            )
            assert redesign.status_code == 201, redesign.json()
            body = redesign.json()
            # a synthetic black reference asset was created and bound
            assert body["reference_asset_id"] != base_asset_id

            async with app.state.session_factory() as db:
                job_row = (
                    await db.execute(
                        select(JobRow).where(JobRow.job_type == "image.edit")
                    )
                ).scalar_one()
                payload = job_row.payload
                assert payload["ipa_weight"] == 0.0
                assert payload["redesign"]["reference_asset_id"] is None
                assert payload["asset_roles"]["reference_image"] == (
                    body["reference_asset_id"]
                )


@pytest.mark.asyncio
async def test_reference_redesign_strength_bounds(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Bounds"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.bounds",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]
            for bad in (0.19, 0.96):
                response = await client.post(
                    f"/api/v1/projects/{project_id}/redesigns",
                    headers=headers,
                    json={
                        "base_revision_id": base_revision_id,
                        "prompt": "restyle",
                        "strength": bad,
                    },
                )
                assert response.status_code == 422, (bad, response.json())


@pytest.mark.asyncio
async def test_reference_redesign_with_explicit_base_asset(settings):
    """A freshly uploaded photo + redesign with explicit base_asset_id works
    end-to-end: 201 and the job binds the uploaded asset as base_image."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Uploaded base"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.uploaded",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]

            # Freshly uploaded photo, no generation manifest lineage at all.
            photo_buffer = BytesIO()
            Image.new("RGB", (1024, 1024), "lightgray").save(
                photo_buffer, format="PNG"
            )
            photo = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("photo.png", photo_buffer.getvalue(), "image/png")},
            )
            assert photo.status_code == 201
            photo_id = photo.json()["id"]

            redesign = await client.post(
                f"/api/v1/projects/{project_id}/redesigns",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "base_asset_id": photo_id,
                    "prompt": "warm scandinavian living room",
                },
            )
            assert redesign.status_code == 201, redesign.json()
            body = redesign.json()
            assert body["base_asset_id"] == photo_id

            async with app.state.session_factory() as db:
                job_row = (
                    await db.execute(
                        select(JobRow).where(JobRow.job_type == "image.edit")
                    )
                ).scalar_one()
                payload = job_row.payload
                assert payload["asset_roles"]["base_image"] == photo_id
                assert payload["redesign"]["base_asset_id"] == photo_id


@pytest.mark.asyncio
async def test_reference_redesign_rejects_non_reference_role(settings):
    """reference_asset_id must be an image asset with role=reference (422)."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Bad ref"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.badref",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]

            # A valid base photo so the request reaches reference validation.
            base_photo = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("photo.png", png_bytes(), "image/png")},
            )
            assert base_photo.status_code == 201
            base_photo_id = base_photo.json()["id"]

            # role=derived, not reference
            derived = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("derived.png", png_bytes(), "image/png")},
            )
            assert derived.status_code == 201
            derived_id = derived.json()["id"]

            response = await client.post(
                f"/api/v1/projects/{project_id}/redesigns",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "base_asset_id": base_photo_id,
                    "reference_asset_id": derived_id,
                    "prompt": "restyle",
                },
            )
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "invalid_reference_asset"


@pytest.mark.asyncio
async def test_noop_revision_conflict_returns_409(settings, monkeypatch):
    """A concurrent same-parent no-op insert surfaces as 409, not 500."""
    from stroy.domain.commands import CommandConflict
    from stroy.api import routes as routes_module

    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project = await client.post(
                "/api/v1/projects", headers=headers, json={"name": "Conflict"}
            )
            project_id = project.json()["id"]
            scene = await client.post(
                f"/api/v1/projects/{project_id}/scene",
                headers=headers,
                json={
                    "scene_id": "scene.conflict",
                    "project_id": project_id,
                    "entities": [],
                    "cameras": [],
                },
            )
            base_revision_id = scene.json()["revision_id"]

            # A valid base photo so the request reaches create_noop_revision.
            base_photo = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("photo.png", png_bytes(), "image/png")},
            )
            assert base_photo.status_code == 201
            base_photo_id = base_photo.json()["id"]

            async def boom(*args, **kwargs):
                raise CommandConflict("base revision was updated concurrently")

            monkeypatch.setattr(routes_module, "create_noop_revision", boom)

            response = await client.post(
                f"/api/v1/projects/{project_id}/redesigns",
                headers=headers,
                json={
                    "base_revision_id": base_revision_id,
                    "base_asset_id": base_photo_id,
                    "prompt": "restyle",
                },
            )
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "revision_conflict"
