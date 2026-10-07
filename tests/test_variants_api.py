"""Variants + budget API e2e tests (R4).

Owner-authenticated HTTP flows over a scratch sqlite app: CRUD and state
machine, the single-approved invariant, fork/restore lineage guards, the
critical isolation AC (variant commands never touch the canonical scene),
compare determinism, budget endpoints, and variant-linked renders (legacy
rows stay NULL).
"""

from __future__ import annotations

from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
import pytest

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.services.assets import MemoryObjectStore

WORKER_HEADERS = {"Authorization": "Bearer worker-secret"}


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


SCENE_ENTITIES = [
    {"id": "room.living", "kind": "room", "geometry": {"wall_ids": ["wall.n"]}},
    {
        "id": "wall.n",
        "kind": "wall",
        "room_id": "room.living",
        "geometry": {
            "a": [0, 0],
            "b": [4000, 0],
            "dimensions_mm": {"length": 4000, "thickness": 100, "height": 2700},
        },
    },
    {
        "id": "floor.main",
        "kind": "floor",
        "room_id": "room.living",
        "geometry": {"bbox": [0, 0, 5000, 4000]},
    },
    {
        "id": "door.main",
        "kind": "door",
        "room_id": "room.living",
        "geometry": {"host_wall_id": "wall.n", "width_mm": 900, "height_mm": 2100},
    },
]

CAMERA = {
    "id": "camera.main",
    "width_px": 800,
    "height_px": 500,
    "intrinsics": {"fx": 620, "fy": 625, "cx": 400, "cy": 250},
    "transform": {"translation_mm": [0, -4500, 1700], "rotation_deg": [78, 0, 0]},
}


async def _setup(client: AsyncClient, headers: dict) -> tuple[str, str]:
    """Project with an initialized scene; returns (project_id, revision_id)."""
    response = await client.post(
        "/api/v1/projects", json={"name": "Flat"}, headers=headers
    )
    assert response.status_code == 201
    project_id = response.json()["id"]

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
    assert scene.status_code == 201
    return project_id, scene.json()["revision_id"]


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


def _set_color(base_revision_id: str, color: str) -> dict:
    return {
        "command_id": f"cmd-{color}",
        "base_revision_id": base_revision_id,
        "operation": "set_color",
        "target_id": "room.living",
        "parameters": {"color": color},
        "origin": "user",
    }


# ---------------------------------------------------------------------------
# CRUD + state machine


async def test_variant_crud_and_archived_filtering(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)

            # CSRF is mandatory on writes.
            anonymous = await client.post(
                f"/api/v1/projects/{project_id}/variants:create-from-current",
                json={"title": "No csrf"},
            )
            assert anonymous.status_code == 403

            variant = await _create_variant(client, headers, project_id, "Red")
            assert variant["status"] == "draft"
            assert variant["base_scene_revision_id"] == revision_id
            assert variant["head_scene_revision_id"] == revision_id

            # Unknown variant + unknown project → 404.
            assert (
                await client.get(f"/api/v1/projects/{project_id}/variants/v.ghost")
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/v1/projects/p.ghost/variants/{variant['id']}"
                )
            ).status_code == 404

            renamed = await client.patch(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}",
                json={"title": "Красный"},
                headers=headers,
            )
            assert renamed.status_code == 200
            assert renamed.json()["title"] == "Красный"

            # Archive → hidden by default, visible with the filter.
            archived = await client.patch(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}",
                json={"status": "archived"},
                headers=headers,
            )
            assert archived.status_code == 200
            listing = await client.get(f"/api/v1/projects/{project_id}/variants")
            assert listing.json() == []
            with_archived = await client.get(
                f"/api/v1/projects/{project_id}/variants",
                params={"include_archived": "true"},
            )
            assert [row["id"] for row in with_archived.json()] == [variant["id"]]
            by_status = await client.get(
                f"/api/v1/projects/{project_id}/variants",
                params={"status": "archived"},
            )
            assert [row["id"] for row in by_status.json()] == [variant["id"]]

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}",
                headers=headers,
            )
            assert deleted.status_code == 204
            assert (
                await client.get(
                    f"/api/v1/projects/{project_id}/variants/{variant['id']}"
                )
            ).status_code == 404


async def test_variant_status_transitions_and_single_approval(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, _ = await _setup(client, headers)
            first = await _create_variant(client, headers, project_id, "First")
            second = await _create_variant(client, headers, project_id, "Second")

            # draft → approved skips the shortlist: 422 with the state code.
            skipped = await client.patch(
                f"/api/v1/projects/{project_id}/variants/{first['id']}",
                json={"status": "approved"},
                headers=headers,
            )
            assert skipped.status_code == 422
            assert skipped.json()["detail"]["code"] == "invalid_status_transition"

            for variant_id in (first["id"], second["id"]):
                assert (
                    await client.patch(
                        f"/api/v1/projects/{project_id}/variants/{variant_id}",
                        json={"status": "shortlisted"},
                        headers=headers,
                    )
                ).status_code == 200
                assert (
                    await client.patch(
                        f"/api/v1/projects/{project_id}/variants/{variant_id}",
                        json={"status": "approved"},
                        headers=headers,
                    )
                ).status_code == 200

            # Second approval demoted the first: exactly ONE approved remains.
            statuses = {
                row["id"]: row["status"]
                for row in (
                    await client.get(f"/api/v1/projects/{project_id}/variants")
                ).json()
            }
            assert statuses == {first["id"]: "shortlisted", second["id"]: "approved"}


# ---------------------------------------------------------------------------
# isolation (critical AC) + lineage guards


async def test_variant_command_leaves_canonical_scene_unchanged(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            variant = await _create_variant(client, headers, project_id, "Paint")

            applied = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}/revisions",
                json={
                    "command": _set_color(revision_id, "red"),
                    "expected_head_revision_id": revision_id,
                },
                headers=headers,
            )
            assert applied.status_code == 200, applied.text
            body = applied.json()
            assert body["parent_revision_id"] == revision_id
            assert body["variant_head_scene_revision_id"] == body["revision_id"]
            room = next(
                e for e in body["scene"]["entities"] if e["id"] == "room.living"
            )
            assert room["metadata"]["color"] == "red"

            # CRITICAL AC: the canonical scene is untouched.
            canonical = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert canonical.json()["revision_id"] == revision_id
            canonical_room = next(
                e
                for e in canonical.json()["scene"]["entities"]
                if e["id"] == "room.living"
            )
            assert "color" not in canonical_room["metadata"]
            assert room["metadata"]["color"] == "red"  # variant kept its own state

            # Canonical commands keep working independently.
            canonical_command = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                json=_set_color(revision_id, "gray"),
                headers=headers,
            )
            assert canonical_command.status_code == 200
            assert (
                await client.get(f"/api/v1/projects/{project_id}/scene")
            ).json()["revision_id"] == canonical_command.json()["revision_id"]
            # The variant head did NOT follow the canonical advance.
            fresh = (
                await client.get(
                    f"/api/v1/projects/{project_id}/variants/{variant['id']}"
                )
            ).json()
            assert fresh["head_scene_revision_id"] == body["revision_id"]

            # Stale expected head → 409 revision_conflict.
            stale = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}/revisions",
                json={
                    "command": _set_color(body["revision_id"], "blue"),
                    "expected_head_revision_id": revision_id,
                },
                headers=headers,
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "revision_conflict"


async def test_fork_and_restore_lineage_guards(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            variant = await _create_variant(client, headers, project_id, "Base")

            # A CANONICAL child of the fork point: in the project, not in the
            # variant's lineage.
            canonical = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                json=_set_color(revision_id, "gray"),
                headers=headers,
            )
            assert canonical.status_code == 200
            canonical_revision = canonical.json()["revision_id"]

            forked_off = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}:fork",
                json={"from_revision_id": canonical_revision},
                headers=headers,
            )
            assert forked_off.status_code == 409
            assert forked_off.json()["detail"]["code"] == "revision_not_in_lineage"

            in_fork = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}:fork",
                json={"title": "From root", "from_revision_id": revision_id},
                headers=headers,
            )
            assert in_fork.status_code == 201
            assert in_fork.json()["base_scene_revision_id"] == revision_id
            assert in_fork.json()["status"] == "draft"

            # Restoring a canonical revision into the variant: 409.
            restored_off = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}"
                "/revisions:restore",
                json={
                    "target_revision_id": canonical_revision,
                    "expected_head_revision_id": revision_id,
                },
                headers=headers,
            )
            assert restored_off.status_code == 409
            assert restored_off.json()["detail"]["code"] == "revision_not_in_lineage"

            # Stale expected head → revision_conflict, checked before lineage.
            stale = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}"
                "/revisions:restore",
                json={
                    "target_revision_id": revision_id,
                    "expected_head_revision_id": canonical_revision,
                },
                headers=headers,
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "revision_conflict"

            # Valid restore: root scene back as a NEW revision.
            restored = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}"
                "/revisions:restore",
                json={
                    "target_revision_id": revision_id,
                    "expected_head_revision_id": revision_id,
                },
                headers=headers,
            )
            assert restored.status_code == 200
            assert restored.json()["parent_revision_id"] == revision_id
            assert restored.json()["revision_id"] != revision_id


async def test_variants_compare_endpoint_is_deterministic(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            left = await _create_variant(client, headers, project_id, "Red")
            right = await _create_variant(client, headers, project_id, "Blue")
            for variant, color in ((left, "red"), (right, "blue")):
                response = await client.post(
                    f"/api/v1/projects/{project_id}/variants/{variant['id']}/revisions",
                    json={
                        "command": _set_color(revision_id, color),
                        "expected_head_revision_id": revision_id,
                    },
                    headers=headers,
                )
                assert response.status_code == 200

            url = (
                f"/api/v1/projects/{project_id}/variants/compare"
                f"?left={left['id']}&right={right['id']}"
            )
            first = await client.get(url)
            second = await client.get(url)
            assert first.status_code == 200
            assert first.text == second.text  # byte-identical responses

            payload = first.json()
            assert payload["left"]["variant_id"] == left["id"]
            assert payload["entities"]["modified"] == [
                {"id": "room.living", "changes": ["metadata.color"]}
            ]
            assert payload["budget"]["delta"] == {
                "contingency": 0.0, "grand_total": 0.0, "known": 0.0
            }

            unknown = await client.get(
                f"/api/v1/projects/{project_id}/variants/compare"
                f"?left={left['id']}&right=v.ghost"
            )
            assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# budget endpoints


async def test_budget_endpoints_e2e(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, _ = await _setup(client, headers)
            variant = await _create_variant(client, headers, project_id, "Смета")
            base = f"/api/v1/projects/{project_id}/variants/{variant['id']}/budget"

            missing = await client.post(f"{base}/items", json={"kind": "manual", "label": "X"})
            assert missing.status_code == 403  # CSRF required

            created = await client.post(
                f"{base}/items",
                json={"kind": "manual", "label": "Работы", "amount": 100},
                headers=headers,
            )
            assert created.status_code == 201, created.text
            assert created.json()["currency"] == "RUB"  # default for amount items
            assert created.json()["scene_revision_id"] == variant["head_scene_revision_id"]

            takeoff = await client.post(
                f"{base}/items",
                json={
                    "kind": "material",
                    "label": "Ламинат",
                    "currency": "RUB",
                    "metadata": {
                        "takeoff": {
                            "coverage_unit": "m2",
                            "target_id": "floor.main",
                            "waste_factor": 0.1,
                            "package_size": 2.5,
                            "unit_price": 890,
                        }
                    },
                },
                headers=headers,
            )
            assert takeoff.status_code == 201

            contingency = await client.post(
                f"{base}/items",
                json={
                    "kind": "manual",
                    "label": "Резерв",
                    "amount": 5000,
                    "currency": "RUB",
                    "metadata": {"contingency": True},
                },
                headers=headers,
            )
            assert contingency.status_code == 201

            items = await client.get(f"{base}/items")
            assert items.status_code == 200
            assert [row["label"] for row in items.json()] == ["Работы", "Ламинат", "Резерв"]

            report = await client.get(f"{base}/report")
            assert report.status_code == 200
            body = report.json()
            # 100 (manual) + 8010 (takeoff: 20 m² → 22 → 9 × 890) + 5000 reserve.
            assert body["totals"] == {
                "known": 8110.0, "contingency": 5000.0, "grand_total": 13110.0
            }
            assert body["currency"] == "RUB"
            assert body["incomplete"] is False

            patched = await client.patch(
                f"{base}/items/{created.json()['id']}",
                json={"amount": 200},
                headers=headers,
            )
            assert patched.status_code == 200
            assert patched.json()["amount"] == 200.0

            removed = await client.delete(
                f"{base}/items/{created.json()['id']}", headers=headers
            )
            assert removed.status_code == 204

            assert (
                await client.get(
                    f"/api/v1/projects/{project_id}/variants/v.ghost/budget/report"
                )
            ).status_code == 404


# ---------------------------------------------------------------------------
# renders × variants


async def _run_render(client: AsyncClient, csrf: str, project_id: str, revision_id: str,
                      *, variant_id: str | None) -> str | None:
    """Drive one render job to completion; returns the variant_id it stored."""
    headers = {"X-CSRF-Token": csrf}
    request_body = {
        "scene_revision_id": revision_id,
        "camera_id": "camera.main",
        "renderer_profile": "blender-cycles-v0",
    }
    if variant_id is not None:
        request_body["variant_id"] = variant_id
    render = await client.post(
        f"/api/v1/projects/{project_id}/renders", json=request_body, headers=headers
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
    # The variant scoping rides the job payload to the worker.
    assert lease["payload"]["variant_id"] == variant_id
    assert lease["payload"]["scene_revision_id"] == revision_id

    pass_assets = {}
    for pass_name in ("rgb", "depth", "normals", "object_ids", "material_ids"):
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

    complete = await client.post(
        f"/api/v1/workers/jobs/{render_job['id']}/complete",
        headers=WORKER_HEADERS,
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
                "scene_metadata": {"schema_version": "0.1.0", "render_seconds": 9.5},
                "output_asset_ids": list(pass_assets.values()),
            },
        },
    )
    assert complete.status_code == 200
    return variant_id


async def test_render_links_variant_and_legacy_rows_stay_null(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            variant = await _create_variant(client, headers, project_id, "Render me")

            # Variant render: revision is the VARIANT head, variant_id recorded.
            await _run_render(
                client, csrf, project_id, revision_id, variant_id=variant["id"]
            )
            # Legacy render: no variant scoping at all.
            await _run_render(
                client, csrf, project_id, revision_id, variant_id=None
            )

            renders = (
                await client.get(f"/api/v1/projects/{project_id}/renders")
            ).json()
            assert len(renders) == 2
            by_variant = {row["variant_id"] for row in renders}
            assert by_variant == {variant["id"], None}
            for row in renders:
                assert row["scene_revision_id"] == revision_id
                assert row["render_seconds"] == 9.5

            detail = await client.get(
                f"/api/v1/renders/{renders[0]['id']}"
            )
            assert detail.status_code == 200
            assert detail.json()["variant_id"] == renders[0]["variant_id"]
