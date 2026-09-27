"""Acceptance tests for pinned base_asset_id in replacements (issue #65)."""

from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import AssetRow, GenerationManifestRow, JobRow
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


def png_bytes(width: int = 3, height: int = 2, color: str = "white") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def login(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": "secret"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


async def _seed_project(client: AsyncClient, headers: dict[str, str]) -> dict:
    """Project with furniture+camera, a manifest-backed base, an independent
    image asset no manifest references, and a valid reference asset."""
    project = await client.post(
        "/api/v1/projects", json={"name": "Pinned"}, headers=headers
    )
    assert project.status_code == 201
    project_id = project.json()["id"]

    scene = await client.post(
        f"/api/v1/projects/{project_id}/scene",
        headers=headers,
        json={
            "scene_id": "scene.pinned",
            "project_id": project_id,
            "entities": [
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
                    "intrinsics": {"fx": 800, "fy": 800, "cx": 500, "cy": 400},
                    "transform": {
                        "translation_mm": [0, -5000, 1500],
                        "rotation_deg": [90, 0, 0],
                    },
                },
            ],
        },
    )
    assert scene.status_code == 201
    base_revision_id = scene.json()["revision_id"]

    base_upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        data={"role": "derived"},
        files={"file": ("room-base.png", png_bytes(64, 48, "lightgray"), "image/png")},
    )
    assert base_upload.status_code == 201
    base_asset_id = base_upload.json()["id"]

    pinned_upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        data={"role": "derived"},
        files={"file": ("pinned.png", png_bytes(32, 32, "blue"), "image/png")},
    )
    assert pinned_upload.status_code == 201
    pinned_asset_id = pinned_upload.json()["id"]

    reference_upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        data={"role": "reference"},
        files={"file": ("chair-reference.png", png_bytes(), "image/png")},
    )
    assert reference_upload.status_code == 201
    reference_id = reference_upload.json()["id"]

    return {
        "project_id": project_id,
        "base_revision_id": base_revision_id,
        "base_asset_id": base_asset_id,
        "pinned_asset_id": pinned_asset_id,
        "reference_id": reference_id,
    }


def _seed_manifest(db, project_id: str, revision_id: str, asset_id: str) -> None:
    db.add(
        GenerationManifestRow(
            id=str(uuid4()),
            project_id=project_id,
            job_id=str(uuid4()),
            scene_revision_id=revision_id,
            design_revision_id=revision_id,
            camera_id="camera.main",
            manifest_json={
                "generation_id": "gen-base",
                "scene_revision_id": revision_id,
                "design_revision_id": revision_id,
                "camera_id": "camera.main",
                "workflow": {"id": "flux-redesign-v0", "version": "0.2.0"},
                "model_profile": "flux-dev-family",
                "seed": 0,
                "input_asset_ids": [],
                "output_asset_ids": [asset_id],
                "structured_conditioning": {},
            },
        )
    )


@pytest.mark.asyncio
async def test_replacement_with_pinned_base_asset(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            ids = await _seed_project(client, headers)

            async with app.state.session_factory() as db:
                _seed_manifest(
                    db, ids["project_id"], ids["base_revision_id"], ids["base_asset_id"]
                )
                await db.commit()

            replacement = await client.post(
                f"/api/v1/projects/{ids['project_id']}/replacements",
                headers=headers,
                json={
                    "base_revision_id": ids["base_revision_id"],
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": ids["reference_id"],
                    "camera_id": "camera.main",
                    "base_asset_id": ids["pinned_asset_id"],
                    "prompt": "replace the sofa with the reference furniture",
                },
            )
            assert replacement.status_code == 201, replacement.json()
            body = replacement.json()
            assert body["base_asset_id"] == ids["pinned_asset_id"]
            assert body["base_asset_id"] != ids["base_asset_id"]

            async with app.state.session_factory() as db:
                job_result = await db.execute(
                    select(JobRow).where(JobRow.job_type == "image.edit")
                )
                job_row = job_result.scalar_one()
                payload = job_row.payload
                assert payload["asset_roles"]["base_image"] == ids["pinned_asset_id"]
                assert payload["asset_roles"]["reference_image"] == ids["reference_id"]
                assert payload["asset_roles"]["mask_image"]
                assert len(payload["input_asset_ids"]) == 3


@pytest.mark.asyncio
async def test_replacement_pinned_base_asset_foreign_project(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            ids = await _seed_project(client, headers)

            foreign = await client.post(
                "/api/v1/projects", json={"name": "Foreign"}, headers=headers
            )
            assert foreign.status_code == 201
            foreign_id = foreign.json()["id"]
            foreign_asset = await client.post(
                f"/api/v1/projects/{foreign_id}/assets",
                headers=headers,
                data={"role": "derived"},
                files={"file": ("foreign.png", png_bytes(), "image/png")},
            )
            assert foreign_asset.status_code == 201
            foreign_asset_id = foreign_asset.json()["id"]

            replacement = await client.post(
                f"/api/v1/projects/{ids['project_id']}/replacements",
                headers=headers,
                json={
                    "base_revision_id": ids["base_revision_id"],
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": ids["reference_id"],
                    "camera_id": "camera.main",
                    "base_asset_id": foreign_asset_id,
                    "prompt": "replace the sofa with the reference furniture",
                },
            )
            assert replacement.status_code == 422
            assert replacement.json()["detail"]["code"] == "invalid_base_asset"


@pytest.mark.asyncio
async def test_replacement_pinned_base_asset_not_image(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            ids = await _seed_project(client, headers)

            pdf_id = str(uuid4())
            async with app.state.session_factory() as db:
                db.add(
                    AssetRow(
                        id=pdf_id,
                        project_id=ids["project_id"],
                        object_key=f"projects/{ids['project_id']}/{pdf_id}.pdf",
                        original_name="document.pdf",
                        media_type="application/pdf",
                        size_bytes=16,
                        sha256=hashlib.sha256(b"%PDF-not-really").hexdigest(),
                        provenance="uploaded",
                        role="derived",
                        source_asset_ids=[],
                        metadata_json={},
                    )
                )
                await db.commit()

            replacement = await client.post(
                f"/api/v1/projects/{ids['project_id']}/replacements",
                headers=headers,
                json={
                    "base_revision_id": ids["base_revision_id"],
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": ids["reference_id"],
                    "camera_id": "camera.main",
                    "base_asset_id": pdf_id,
                    "prompt": "replace the sofa with the reference furniture",
                },
            )
            assert replacement.status_code == 422
            assert replacement.json()["detail"]["code"] == "invalid_base_asset"


@pytest.mark.asyncio
async def test_replacement_pinned_base_asset_missing(settings):
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            ids = await _seed_project(client, headers)

            replacement = await client.post(
                f"/api/v1/projects/{ids['project_id']}/replacements",
                headers=headers,
                json={
                    "base_revision_id": ids["base_revision_id"],
                    "target_entity_id": "object.sofa.main",
                    "reference_asset_id": ids["reference_id"],
                    "camera_id": "camera.main",
                    "base_asset_id": str(uuid4()),
                    "prompt": "replace the sofa with the reference furniture",
                },
            )
            assert replacement.status_code == 422
            assert replacement.json()["detail"]["code"] == "invalid_base_asset"
