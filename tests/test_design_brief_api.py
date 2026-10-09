"""R8 designer brief API e2e tests (#198).

Owner-authenticated HTTP flows over a scratch sqlite app:

- brief-notes endpoints: nulls before any write, PATCH round-trip with
  partial-update semantics, whitespace→null normalization, ≤8000 limit,
  CSRF enforcement;
- design-brief aggregate: auth, variant resolution (explicit binding,
  approved fallback, 409 without any approved variant, foreign variant 404),
  omitempty sections on a fresh minimal project, plan-scale status mapping,
  renders (rgb asset + concept label + stage, legacy rows excluded),
  furniture-intent grouping incl. the locked duplicate, and the read-only
  guarantee (row counts of scene_revisions/render_manifests/assets unchanged).
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import func, select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import AssetRow, ProjectRow, RenderManifestRow, SceneRevisionRow
from stroy.services.assets import MemoryObjectStore

WORKER_HEADERS = {"Authorization": "Bearer worker-secret"}

BRIEF_WARNING_DOC = (
    "Бриф не является строительной или рабочей документацией; "
    "детали требуют проверки специалистом."
)
BRIEF_WARNING_CONCEPT = "Сгенерированные изображения — концепты, а не фотографии."
RENDER_CONCEPT_LABEL = "Концепт, не фотография"

# Fresh scene: furniture only (no rooms), no intents, no locks — the brief
# must omit rooms/furniture_intents until intents/locks/rooms exist.
SCENE_ENTITIES = [
    {"id": "object.sofa.main", "kind": "furniture", "display_name": "Диван"},
    {"id": "object.chair.main", "kind": "furniture", "room_id": "room.living"},
    {"id": "object.table.main", "kind": "furniture"},
    {"id": "object.wardrobe.main", "kind": "furniture"},
    {"id": "surface.wall.north", "kind": "wall"},
]

CAMERA = {
    "id": "camera.main",
    "width_px": 800,
    "height_px": 500,
    "intrinsics": {"fx": 620, "fy": 625, "cx": 400, "cy": 250},
    "transform": {"translation_mm": [0, -4500, 1700], "rotation_deg": [78, 0, 0]},
}


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


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (3, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


async def _create_project(client: AsyncClient, headers: dict, name: str = "Flat") -> str:
    response = await client.post(
        "/api/v1/projects", json={"name": name}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _init_scene(
    client: AsyncClient, headers: dict, project_id: str
) -> tuple[str, str]:
    """POST the default scene; returns (revision_id, content_hash)."""
    scene = await client.post(
        f"/api/v1/projects/{project_id}/scene",
        headers=headers,
        json={
            "scene_id": "scene.main",
            "project_id": project_id,
            "entities": SCENE_ENTITIES,
            "cameras": [CAMERA],
        },
    )
    assert scene.status_code == 201, scene.text
    return scene.json()["revision_id"], scene.json()["content_hash"]


async def _create_variant(
    client: AsyncClient, headers: dict, project_id: str, title: str
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/variants:create-from-current",
        json={"title": title},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _approve_variant(
    client: AsyncClient, headers: dict, project_id: str, variant_id: str
) -> None:
    for status in ("shortlisted", "approved"):
        response = await client.patch(
            f"/api/v1/projects/{project_id}/variants/{variant_id}",
            json={"status": status},
            headers=headers,
        )
        assert response.status_code == 200, response.text


async def _apply_variant_command(
    client: AsyncClient,
    headers: dict,
    project_id: str,
    variant_id: str,
    head_id: str,
    operation: str,
    target_id: str,
    parameters: dict,
) -> str:
    """Apply one command on the variant head; returns the new head revision."""
    response = await client.post(
        f"/api/v1/projects/{project_id}/variants/{variant_id}/revisions",
        json={
            "command": {
                "command_id": f"cmd-{uuid4()}",
                "base_revision_id": head_id,
                "operation": operation,
                "target_id": target_id,
                "parameters": parameters,
                "origin": "user",
            },
            "expected_head_revision_id": head_id,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["revision_id"]


def plan_draft(scale: dict) -> dict:
    """Minimal one-room plan draft with the given scale block."""

    def wall(wall_id: str, x1: float, y1: float, x2: float, y2: float) -> dict:
        return {
            "id": wall_id,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "thickness_mm": 100,
            "openings": [],
        }

    return {
        "version": "0.1.0",
        "units": "mm",
        "scale": scale,
        "floors": [
            {
                "name": "main",
                "level_mm": 0.0,
                "walls": [
                    wall("wall.n", 0, 0, 4000, 0),
                    wall("wall.e", 4000, 0, 4000, 3000),
                    wall("wall.s", 4000, 3000, 0, 3000),
                    wall("wall.w", 0, 3000, 0, 0),
                ],
                "rooms": [
                    {
                        "id": "room.living",
                        "name": "Гостиная",
                        "wall_ids": ["wall.n", "wall.e", "wall.s", "wall.w"],
                        "floor_finish": None,
                    }
                ],
            }
        ],
    }


async def _save_plan_draft(
    client: AsyncClient, headers: dict, project_id: str, scale: dict
) -> None:
    response = await client.put(
        f"/api/v1/projects/{project_id}/plan/draft",
        headers=headers,
        json={"draft": plan_draft(scale)},
    )
    assert response.status_code == 200, response.text


async def _run_render(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    revision_id: str,
    *,
    variant_id: str | None,
    stage: str,
    rgb: bool = True,
    model_provenance: dict | None = None,
) -> tuple[str, str | None]:
    """Drive one render job to completion; returns (render_id, rgb_asset_id).

    ``rgb=False`` completes with a manifest whose passes lack the rgb pass
    (the persisted row then has no preview image for the brief).
    """
    headers = {"X-CSRF-Token": csrf}
    body: dict = {
        "scene_revision_id": revision_id,
        "camera_id": "camera.main",
        "renderer_profile": "blender-cycles-v0",
        "stage": stage,
    }
    if variant_id is not None:
        body["variant_id"] = variant_id
    render = await client.post(
        f"/api/v1/projects/{project_id}/renders", json=body, headers=headers
    )
    assert render.status_code == 201, render.text
    render_job = render.json()

    register = await client.post(
        "/api/v1/workers/register",
        headers=WORKER_HEADERS,
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
        headers=WORKER_HEADERS,
        json={"worker_id": "blender-worker"},
    )
    assert claim.status_code == 200
    lease = claim.json()
    assert lease["job_id"] == render_job["id"]
    assert lease["payload"]["variant_id"] == variant_id

    pass_names = (
        ("rgb", "depth", "normals", "object_ids", "material_ids")
        if rgb
        else ("depth",)
    )
    pass_assets: dict[str, str] = {}
    for pass_name in pass_names:
        media_type = "image/png" if pass_name == "rgb" else "image/x-exr"
        suffix = "png" if pass_name == "rgb" else "exr"
        upload = await client.post(
            f"/api/v1/workers/jobs/{render_job['id']}/outputs",
            headers=WORKER_HEADERS,
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

    manifest: dict = {
        "schema_version": "0.1.0",
        "render_id": lease["payload"]["render_id"],
        "scene_revision_id": revision_id,
        "camera_id": "camera.main",
        "renderer_profile": "blender-cycles-v0",
        "stage": stage,
        "passes": pass_assets,
    }
    if model_provenance is not None:
        manifest["model_provenance"] = model_provenance
    complete = await client.post(
        f"/api/v1/workers/jobs/{render_job['id']}/complete",
        headers=WORKER_HEADERS,
        json={
            "worker_id": "blender-worker",
            "lease_id": lease["lease_id"],
            "result": {
                "render_manifest": manifest,
                "scene_metadata": {"schema_version": "0.1.0", "render_seconds": 9.5},
                "output_asset_ids": list(pass_assets.values()),
            },
        },
    )
    assert complete.status_code == 200, complete.text
    return manifest["render_id"], pass_assets.get("rgb")


async def _row_count(app, model) -> int:
    async with app.state.session_factory() as db:
        return (
            await db.execute(select(func.count()).select_from(model))
        ).scalar_one()


# ---------------------------------------------------------------------------
# auth


async def test_design_brief_and_brief_notes_require_auth(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (
                await client.get("/api/v1/projects/p.ghost/design-brief")
            ).status_code == 401
            assert (
                await client.get("/api/v1/projects/p.ghost/brief-notes")
            ).status_code == 401
            assert (
                await client.patch(
                    "/api/v1/projects/p.ghost/brief-notes",
                    json={"needs_wishes": "x"},
                )
            ).status_code == 401

            # Authenticated but unknown project → 404 (not 401/409).
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            missing_brief = await client.get(
                "/api/v1/projects/p.ghost/design-brief", headers=headers
            )
            assert missing_brief.status_code == 404
            assert missing_brief.json()["detail"] == "project not found"
            missing_notes = await client.get(
                "/api/v1/projects/p.ghost/brief-notes", headers=headers
            )
            assert missing_notes.status_code == 404


# ---------------------------------------------------------------------------
# brief notes


async def test_brief_notes_get_returns_nulls_before_any_patch(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            response = await client.get(
                f"/api/v1/projects/{project_id}/brief-notes"
            )
            assert response.status_code == 200
            assert response.json() == {
                "needs_wishes": None,
                "questions_to_discuss": None,
            }


async def test_brief_notes_patch_round_trip_partial_and_csrf(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            await _approve_variant(client, headers, project_id, variant["id"])

            # CSRF is mandatory on writes.
            anonymous = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "no csrf"},
            )
            assert anonymous.status_code == 403

            # Round-trip: write both fields.
            written = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={
                    "needs_wishes": "Тёплый свет, дерево",
                    "questions_to_discuss": "Куда ставить ТВ?",
                },
                headers=headers,
            )
            assert written.status_code == 200, written.text
            assert written.json() == {
                "needs_wishes": "Тёплый свет, дерево",
                "questions_to_discuss": "Куда ставить ТВ?",
            }
            fetched = await client.get(f"/api/v1/projects/{project_id}/brief-notes")
            assert fetched.json() == written.json()

            # Stored shape keeps updated_at server-side.
            async with app.state.session_factory() as db:
                row = await db.get(ProjectRow, project_id)
                assert set(row.brief_notes_json) == {
                    "needs_wishes",
                    "questions_to_discuss",
                    "updated_at",
                }
                assert row.brief_notes_json["updated_at"]

            # Partial update: only one field, the other is preserved.
            partial = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "Добавить складной диван"},
                headers=headers,
            )
            assert partial.status_code == 200
            assert partial.json() == {
                "needs_wishes": "Добавить складной диван",
                "questions_to_discuss": "Куда ставить ТВ?",
            }

            # Notes surface in the design brief (approved fallback).
            brief = await client.get(f"/api/v1/projects/{project_id}/design-brief")
            assert brief.status_code == 200
            assert brief.json()["notes"] == {
                "needs_wishes": "Добавить складной диван",
                "questions_to_discuss": "Куда ставить ТВ?",
            }

            # Explicit null clears exactly one field.
            cleared_one = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"questions_to_discuss": None},
                headers=headers,
            )
            assert cleared_one.json() == {
                "needs_wishes": "Добавить складной диван",
                "questions_to_discuss": None,
            }

            # Clearing the last field removes the notes section entirely.
            cleared_all = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": None},
                headers=headers,
            )
            assert cleared_all.json() == {
                "needs_wishes": None,
                "questions_to_discuss": None,
            }
            brief_after = await client.get(
                f"/api/v1/projects/{project_id}/design-brief"
            )
            assert "notes" not in brief_after.json()


async def test_brief_notes_normalizes_whitespace_to_null(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            # Whitespace-only values normalize to null on write.
            spaced = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "   ", "questions_to_discuss": "\n\t "},
                headers=headers,
            )
            assert spaced.status_code == 200
            assert spaced.json() == {
                "needs_wishes": None,
                "questions_to_discuss": None,
            }
            async with app.state.session_factory() as db:
                row = await db.get(ProjectRow, project_id)
                # Both null → the column itself stays NULL (never {}).
                assert row.brief_notes_json is None

            # A real value sticks, then a whitespace PATCH clears one field.
            filled = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "Реальные пожелания"},
                headers=headers,
            )
            assert filled.json()["needs_wishes"] == "Реальные пожелания"
            blanked = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "  "},
                headers=headers,
            )
            assert blanked.status_code == 200
            assert blanked.json() == {
                "needs_wishes": None,
                "questions_to_discuss": None,
            }
            async with app.state.session_factory() as db:
                row = await db.get(ProjectRow, project_id)
                assert row.brief_notes_json is None


async def test_brief_notes_rejects_over_8000_chars(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            too_long = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "x" * 8001},
                headers=headers,
            )
            assert too_long.status_code == 422

            # Exactly 8000 chars is accepted.
            exact = await client.patch(
                f"/api/v1/projects/{project_id}/brief-notes",
                json={"needs_wishes": "x" * 8000},
                headers=headers,
            )
            assert exact.status_code == 200
            assert exact.json()["needs_wishes"] == "x" * 8000


# ---------------------------------------------------------------------------
# design brief: variant resolution


async def test_design_brief_requires_approved_variant(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)

            # No variants at all → 409 brief_variant_required.
            none_yet = await client.get(
                f"/api/v1/projects/{project_id}/design-brief"
            )
            assert none_yet.status_code == 409
            assert none_yet.json()["detail"]["code"] == "brief_variant_required"

            # A draft variant is not enough.
            await _create_variant(client, headers, project_id, "Черновик")
            still = await client.get(f"/api/v1/projects/{project_id}/design-brief")
            assert still.status_code == 409
            assert still.json()["detail"]["code"] == "brief_variant_required"


async def test_design_brief_explicit_variant_binds_and_foreign_404(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id, content_hash = await _init_scene(client, headers, project_id)
            first = await _create_variant(client, headers, project_id, "Первый")
            second = await _create_variant(client, headers, project_id, "Второй")

            # Explicit variant_id binds the returned variant (even a draft).
            bound = await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": second["id"]},
            )
            assert bound.status_code == 200, bound.text
            variant = bound.json()["variant"]
            assert variant["id"] == second["id"]
            assert variant["title"] == "Второй"
            assert variant["status"] == "draft"
            assert variant["head_scene_revision_id"] == revision_id
            assert variant["head_scene_revision_hash"] == content_hash
            assert variant["head_scene_revision_created_at"] is not None

            # Unknown variant id → 404.
            unknown = await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": "v.ghost"},
            )
            assert unknown.status_code == 404
            assert unknown.json()["detail"] == "variant not found"

            # A variant from ANOTHER project → 404 (project-scoped lookup).
            other_project = await _create_project(client, headers, name="Другая")
            foreign = await client.get(
                f"/api/v1/projects/{other_project}/design-brief",
                params={"variant_id": first["id"]},
            )
            assert foreign.status_code == 404
            assert foreign.json()["detail"] == "variant not found"

            # Photos: reference-role asset + photo attachment with R7 mapping.
            upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "reference"},
                files={"file": ("photo1.png", png_bytes(), "image/png")},
            )
            assert upload.status_code == 201
            attachment = await client.post(
                f"/api/v1/projects/{project_id}/attachments",
                headers=headers,
                json={
                    "target_type": "project",
                    "kind": "photo",
                    "asset_id": upload.json()["id"],
                    "metadata": {"mapping": "owner_room", "confidence": "approx"},
                },
            )
            assert attachment.status_code == 201, attachment.text
            brief = await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": first["id"]},
            )
            photos = brief.json()["photos"]
            assert len(photos) == 1
            assert photos[0]["attachment_id"] == attachment.json()["id"]
            assert photos[0]["asset_id"] == upload.json()["id"]
            assert photos[0]["caption"] == "photo1.png"
            assert photos[0]["mapping"] == {
                "mapping": "owner_room",
                "confidence": "approx",
            }

            # A reference-role asset is not a plan source: no plan section.
            assert "plan" not in brief.json()


async def test_design_brief_falls_back_to_approved_variant(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            await _init_scene(client, headers, project_id)
            first = await _create_variant(client, headers, project_id, "Первый")
            second = await _create_variant(client, headers, project_id, "Второй")

            # No variant_id → the single approved variant is used.
            await _approve_variant(client, headers, project_id, second["id"])
            brief = await client.get(f"/api/v1/projects/{project_id}/design-brief")
            assert brief.status_code == 200
            assert brief.json()["variant"]["id"] == second["id"]
            assert brief.json()["variant"]["status"] == "approved"
            assert first["id"] != second["id"]


# ---------------------------------------------------------------------------
# design brief: omitempty


async def test_design_brief_fresh_project_omits_empty_sections(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers, name="Свежий")
            revision_id, content_hash = await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            await _approve_variant(client, headers, project_id, variant["id"])

            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief"
            )).json()

            # Only the always-present sections survive omitempty.
            assert set(brief) == {"schema_version", "project", "variant", "warnings"}
            assert brief["schema_version"] == "1.0"
            assert brief["project"]["name"] == "Свежий"
            assert brief["project"]["id"] == project_id
            assert brief["project"]["created_at"] is not None
            assert brief["variant"]["id"] == variant["id"]
            assert brief["variant"]["head_scene_revision_id"] == revision_id
            assert brief["variant"]["head_scene_revision_hash"] == content_hash
            assert brief["warnings"] == [BRIEF_WARNING_DOC, BRIEF_WARNING_CONCEPT]

            # No empty arrays anywhere: notes/plan/renders/photos/style/budget
            # and rooms/furniture_intents are all absent on a fresh project.
            for section in (
                "notes",
                "plan",
                "rooms",
                "furniture_intents",
                "renders",
                "photos",
                "style_direction",
                "budget",
            ):
                assert section not in brief


# ---------------------------------------------------------------------------
# design brief: plan scale


async def test_design_brief_scale_status_mapping(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)

            # manual → confirmed (commit per plan harness builds the scene).
            await _save_plan_draft(
                client, headers, project_id, {"source": "manual", "mm_per_px": 1.0}
            )
            commit = await client.post(
                f"/api/v1/projects/{project_id}/plan/draft/commit",
                headers=headers,
            )
            assert commit.status_code == 200, commit.text
            variant = await _create_variant(client, headers, project_id, "V")
            await _approve_variant(client, headers, project_id, variant["id"])

            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief"
            )).json()
            scale = brief["plan"]["scale"]
            assert scale["status"] == "confirmed"
            assert scale["source"] == "manual"
            assert scale["mm_per_px"] == 1.0
            assert scale["label"]

            # Rooms come from the variant head (plan-committed scene).
            rooms = brief["rooms"]
            assert [room["id"] for room in rooms] == ["room.living"]
            assert rooms[0]["name"] == "Гостиная"

            # plan_label → approximate (latest draft wins).
            await _save_plan_draft(
                client, headers, project_id, {"source": "plan_label", "mm_per_px": 0.5}
            )
            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief"
            )).json()
            scale = brief["plan"]["scale"]
            assert scale["status"] == "approximate"
            assert scale["source"] == "plan_label"
            assert scale["mm_per_px"] == 0.5

            # unknown → unknown (mm_per_px stays absent).
            await _save_plan_draft(
                client, headers, project_id, {"source": "unknown"}
            )
            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief"
            )).json()
            scale = brief["plan"]["scale"]
            assert scale["status"] == "unknown"
            assert scale["source"] == "unknown"
            assert "mm_per_px" not in scale


# ---------------------------------------------------------------------------
# design brief: renders


async def test_design_brief_renders_rgb_asset_label_and_stage(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id, _ = await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            await _approve_variant(client, headers, project_id, variant["id"])

            draft_render, draft_rgb = await _run_render(
                client,
                csrf,
                project_id,
                revision_id,
                variant_id=variant["id"],
                stage="draft",
                model_provenance={"provider": "qwen", "model": "qwen-image-edit"},
            )
            final_render, final_rgb = await _run_render(
                client, csrf, project_id, revision_id,
                variant_id=variant["id"], stage="final",
            )
            legacy_render, _legacy_rgb = await _run_render(
                client, csrf, project_id, revision_id,
                variant_id=None, stage="final",
            )

            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": variant["id"]},
            )).json()

            renders = brief["renders"]
            assert [row["id"] for row in renders] == [final_render, draft_render]
            by_id = {row["id"]: row for row in renders}
            assert by_id[draft_render]["asset_id"] == draft_rgb
            assert by_id[final_render]["asset_id"] == final_rgb
            assert by_id[draft_render]["label"] == RENDER_CONCEPT_LABEL
            assert by_id[final_render]["label"] == RENDER_CONCEPT_LABEL
            assert by_id[draft_render]["stage"] == "draft"
            assert by_id[final_render]["stage"] == "final"
            assert by_id[draft_render]["camera_id"] == "camera.main"
            assert by_id[draft_render]["renderer_profile"] == "blender-cycles-v0"
            assert by_id[draft_render]["created_at"] is not None
            assert by_id[draft_render]["workflow_model_provenance"] == {
                "model": {"provider": "qwen", "model": "qwen-image-edit"}
            }
            # The legacy NULL-variant render is excluded; no provenance on final.
            assert legacy_render not in by_id
            assert "workflow_model_provenance" not in by_id[final_render]


async def test_design_brief_renders_cap_twelve_after_rgb_filter(settings) -> None:
    """M1: 13 rgb-ful renders → exactly the 12 newest (the cap is applied
    after the rgb filter, never below it)."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id, _ = await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            variant_id = variant["id"]

            render_ids: list[str] = []
            for _index in range(13):
                render_id, rgb_asset_id = await _run_render(
                    client,
                    csrf,
                    project_id,
                    revision_id,
                    variant_id=variant_id,
                    stage="final",
                )
                assert rgb_asset_id is not None
                render_ids.append(render_id)

            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": variant_id},
            )).json()

            renders = brief["renders"]
            assert len(renders) == 12
            # Newest first: the last-completed render leads, the oldest of
            # the 13 is cut by the cap.
            assert [row["id"] for row in renders] == render_ids[12:0:-1]
            assert render_ids[0] not in {row["id"] for row in renders}
            # No skipped-render warning: every render had an rgb pass.
            assert brief["warnings"] == [BRIEF_WARNING_DOC, BRIEF_WARNING_CONCEPT]


async def test_design_brief_warns_on_skipped_rgb_less_renders(settings) -> None:
    """M2: rgb-less manifests are excluded from the renders list and surface
    a single (deduped) warning."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id, _ = await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            variant_id = variant["id"]

            # Two saved renders without an rgb pass + one displayable.
            skipped_ids: list[str] = []
            for _ in range(2):
                render_id, rgb_asset_id = await _run_render(
                    client,
                    csrf,
                    project_id,
                    revision_id,
                    variant_id=variant_id,
                    stage="final",
                    rgb=False,
                )
                assert rgb_asset_id is None
                skipped_ids.append(render_id)
            shown_render, shown_rgb = await _run_render(
                client, csrf, project_id, revision_id,
                variant_id=variant_id, stage="draft",
            )

            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": variant_id},
            )).json()

            renders = brief["renders"]
            assert [row["id"] for row in renders] == [shown_render]
            assert renders[0]["asset_id"] == shown_rgb
            assert renders[0]["stage"] == "draft"
            for skipped_id in skipped_ids:
                assert skipped_id not in {row["id"] for row in renders}
            # Both skips collapse into ONE deduped warning entry.
            assert brief["warnings"] == [
                BRIEF_WARNING_DOC,
                BRIEF_WARNING_CONCEPT,
                (
                    "Некоторые сохранённые рендеры пропущены: "
                    "нет изображения для предпросмотра."
                ),
            ]


# ---------------------------------------------------------------------------
# design brief: furniture intents


async def test_design_brief_furniture_intents_grouping_with_locked(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id, _ = await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            variant_id = variant["id"]
            head = variant["head_scene_revision_id"]
            assert head == revision_id

            # SET_INTENT / SET_LOCKS commands on the variant head.
            head = await _apply_variant_command(
                client, headers, project_id, variant_id, head,
                "set_intent", "object.sofa.main", {"intent": "keep"},
            )
            head = await _apply_variant_command(
                client, headers, project_id, variant_id, head,
                "set_intent", "object.chair.main", {"intent": "remove"},
            )
            head = await _apply_variant_command(
                client, headers, project_id, variant_id, head,
                "set_intent", "object.table.main", {"intent": "replace"},
            )
            # Locked duplicate: keep intent AND an existence lock.
            head = await _apply_variant_command(
                client, headers, project_id, variant_id, head,
                "set_intent", "object.wardrobe.main", {"intent": "keep"},
            )
            head = await _apply_variant_command(
                client, headers, project_id, variant_id, head,
                "set_locks", "object.wardrobe.main", {"locks": {"existence": True}},
            )

            brief = (await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": variant_id},
            )).json()

            intents = brief["furniture_intents"]
            assert set(intents) == {"keep", "remove", "replace", "locked"}
            assert [entry["id"] for entry in intents["keep"]] == [
                "object.sofa.main",
                "object.wardrobe.main",
            ]
            assert [entry["id"] for entry in intents["remove"]] == [
                "object.chair.main"
            ]
            assert [entry["id"] for entry in intents["replace"]] == [
                "object.table.main"
            ]
            # Locked duplicates the keep-pinned wardrobe.
            assert [entry["id"] for entry in intents["locked"]] == [
                "object.wardrobe.main"
            ]
            assert intents["keep"][0]["name"] == "Диван"
            assert intents["keep"][0]["kind"] == "furniture"
            assert intents["remove"][0]["room_id"] == "room.living"
            locked_entry = intents["locked"][0]
            assert locked_entry["locks"]["existence"] is True
            keep_wardrobe = next(
                entry
                for entry in intents["keep"]
                if entry["id"] == "object.wardrobe.main"
            )
            assert keep_wardrobe["locks"]["existence"] is True
            # The unlocked wall appears in no group.
            all_ids = {
                entry["id"]
                for group in intents.values()
                for entry in group
            }
            assert "surface.wall.north" not in all_ids
            assert intents["keep"][1]["kind"] == "furniture"


# ---------------------------------------------------------------------------
# design brief: read-only guarantee


async def test_design_brief_is_read_only(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id = await _create_project(client, headers)
            revision_id, _ = await _init_scene(client, headers, project_id)
            variant = await _create_variant(client, headers, project_id, "V")
            await _approve_variant(client, headers, project_id, variant["id"])
            await _run_render(
                client, csrf, project_id, revision_id,
                variant_id=variant["id"], stage="final",
            )
            upload = await client.post(
                f"/api/v1/projects/{project_id}/assets",
                headers=headers,
                data={"role": "plan"},
                files={"file": ("plan.png", png_bytes(), "image/png")},
            )
            assert upload.status_code == 201

            before = {
                SceneRevisionRow: await _row_count(app, SceneRevisionRow),
                RenderManifestRow: await _row_count(app, RenderManifestRow),
                AssetRow: await _row_count(app, AssetRow),
            }

            first = await client.get(f"/api/v1/projects/{project_id}/design-brief")
            assert first.status_code == 200
            second = await client.get(
                f"/api/v1/projects/{project_id}/design-brief",
                params={"variant_id": variant["id"]},
            )
            assert second.status_code == 200

            after = {
                SceneRevisionRow: await _row_count(app, SceneRevisionRow),
                RenderManifestRow: await _row_count(app, RenderManifestRow),
                AssetRow: await _row_count(app, AssetRow),
            }
            assert before == after

            # The plan asset (role "plan") surfaces with the draft-less scale.
            plan = first.json()["plan"]
            assert plan["asset_id"] == upload.json()["id"]
            assert plan["scale"]["status"] == "unknown"
            assert plan["scale"]["source"] == "unknown"
