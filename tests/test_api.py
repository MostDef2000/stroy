from __future__ import annotations

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
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
            assert (await client.get("/health")).status_code == 200
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

            job_response = await client.post(
                f"/api/v1/projects/{project_id}/jobs",
                headers=headers,
                json={
                    "job_type": "image.generate",
                    "payload": {},
                    "required_capabilities": ["image_generation"],
                },
            )
            assert job_response.status_code == 201
            job_id = job_response.json()["id"]

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
            assert any(
                item["id"] == output_asset_id
                and item["provenance"] == "model_inferred"
                for item in assets.json()
            )

            workers = await client.get("/api/v1/workers")
            assert workers.status_code == 200
            assert workers.json()[0]["online"] is True

            complete = await client.post(
                f"/api/v1/workers/jobs/{job_id}/complete",
                headers=worker_headers,
                json={
                    "worker_id": "worker-1",
                    "lease_id": lease["lease_id"],
                    "result": {"image": "fake"},
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
