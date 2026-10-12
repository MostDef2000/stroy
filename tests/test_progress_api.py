"""Progress snapshots + planned-vs-actual deviations API tests (R12, #162).

Owner-authenticated HTTP flows over a scratch sqlite app: snapshot CRUD with
compare-target pinning (canonical latest / approved variant head, never
re-resolved), owner-recorded deviations (no photo inference), the deviation
status transition table, soft delete, IDOR scoping, compare (counts + planned
dangling flags), and the accept orchestration — confirmed deviations applied
as real scene commands (canonical CAS / variant expected-head CAS).
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import jsonschema
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from PIL import Image
import pytest
from sqlalchemy import func, select

from stroy.api.app import create_app
from stroy.config import Settings
from stroy.db.models import DeviationRow, SceneRevisionRow
from stroy.services.assets import MemoryObjectStore

ROOT = Path(__file__).resolve().parents[1]


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
    {"id": "object.sofa.main", "kind": "furniture", "room_id": "room.living"},
]

CAMERA = {
    "id": "camera.main",
    "width_px": 800,
    "height_px": 500,
    "intrinsics": {"fx": 620, "fy": 625, "cx": 400, "cy": 250},
    "transform": {"translation_mm": [0, -4500, 1700], "rotation_deg": [78, 0, 0]},
}


async def _create_project(client: AsyncClient, headers: dict, name: str = "Flat") -> str:
    response = await client.post(
        "/api/v1/projects", json={"name": name}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _setup(
    client: AsyncClient, headers: dict, name: str = "Flat"
) -> tuple[str, str]:
    """Project with an initialized scene; returns (project_id, revision_id)."""
    project_id = await _create_project(client, headers, name)
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
    return project_id, scene.json()["revision_id"]


async def _upload_photo(client: AsyncClient, headers: dict, project_id: str) -> str:
    uploaded = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        headers=headers,
        files={"file": ("room.png", png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["id"]


async def _create_attachment(
    client: AsyncClient, headers: dict, project_id: str, asset_id: str
) -> str:
    created = await client.post(
        f"/api/v1/projects/{project_id}/attachments",
        headers=headers,
        json={"target_type": "project", "kind": "photo", "asset_id": asset_id},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _snapshot_payload(**overrides) -> dict:
    payload = {
        "name": "Demolition done",
        "stage": "demolition",
        "captured_at": "2026-10-12T10:00:00Z",
        "provenance": "manually_measured",
        "note": "walls stripped",
    }
    payload.update(overrides)
    return payload


async def _create_snapshot(
    client: AsyncClient, headers: dict, project_id: str, **overrides
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/progress/snapshots",
        json=_snapshot_payload(**overrides),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _deviation_payload(**overrides) -> dict:
    payload = {
        "planned": {
            "target_type": "entity",
            "entity_id": "object.sofa.main",
            "property_path": "material_ref",
            "value": "material.oak",
        },
        "observed": {
            "kind": "property_diff",
            "value": {"path": "material_ref", "actual": "material.laminate"},
        },
        "provenance": "observed",
        "confidence": "medium",
    }
    payload.update(overrides)
    return payload


async def _create_deviation(
    client: AsyncClient, headers: dict, project_id: str, snapshot_id: str, **overrides
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/progress/snapshots/{snapshot_id}/deviations",
        json=_deviation_payload(**overrides),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _confirm_deviation(
    client: AsyncClient, headers: dict, project_id: str, deviation_id: str
) -> dict:
    response = await client.patch(
        f"/api/v1/projects/{project_id}/progress/deviations/{deviation_id}",
        json={"status": "confirmed"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_approved_variant(
    client: AsyncClient, headers: dict, project_id: str, title: str = "Blue"
) -> dict:
    created = await client.post(
        f"/api/v1/projects/{project_id}/variants:create-from-current",
        json={"title": title},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    variant = created.json()
    for status in ("shortlisted", "approved"):
        patched = await client.patch(
            f"/api/v1/projects/{project_id}/variants/{variant['id']}",
            json={"status": status},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
    variant["status"] = "approved"
    return variant


def _accept_command(
    deviation_id: str,
    base_revision_id: str,
    *,
    command_id: str = "cmd-accept-1",
    target_id: str = "room.living",
) -> dict:
    """accept_confirmed_deviation envelope wrapping a plain set_color."""
    return {
        "schema_version": "0.1.0",
        "command_id": command_id,
        "base_revision_id": base_revision_id,
        "operation": "accept_confirmed_deviation",
        "target_id": target_id,
        "parameters": {
            "deviation_id": deviation_id,
            "change": {
                "schema_version": "0.1.0",
                "command_id": f"{command_id}-change",
                "base_revision_id": base_revision_id,
                "operation": "set_color",
                "target_id": target_id,
                "parameters": {"color": "#ff0000"},
                "origin": "user",
            },
        },
        "origin": "user",
    }


def _accept_request(
    deviation_id: str,
    base_revision_id: str,
    *,
    command_id: str = "cmd-accept-1",
    target_kind: str = "canonical",
    variant_id: str | None = None,
    expected_head_revision_id: str | None = None,
) -> dict:
    target: dict = {"kind": target_kind}
    if variant_id is not None:
        target["variant_id"] = variant_id
    if expected_head_revision_id is not None:
        target["expected_head_revision_id"] = expected_head_revision_id
    return {
        "target": target,
        "command": _accept_command(deviation_id, base_revision_id, command_id=command_id),
    }


def _design_command_schema() -> dict:
    return json.loads(
        (ROOT / "schemas" / "design-command.schema.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# AC 1/2/6: snapshot CRUD, persisted fields, project-scoped attachments


async def test_snapshot_create_persists_named_fields(settings) -> None:
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
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(),
            )
            assert anonymous.status_code == 403

            snapshot = await _create_snapshot(client, headers, project_id)
            assert snapshot["name"] == "Demolition done"
            assert snapshot["stage"] == "demolition"
            assert snapshot["stage_label"] is None
            assert snapshot["captured_at"].startswith("2026-10-12T10:00:00")
            assert snapshot["provenance"] == "manually_measured"
            assert snapshot["note"] == "walls stripped"
            assert snapshot["attachment_ids"] == []
            assert snapshot["deleted_at"] is None
            # Omitted compare_target pins the CURRENT canonical latest.
            assert snapshot["compare_target"] == {
                "kind": "canonical",
                "variant_id": None,
                "revision_id": revision_id,
            }

            fetched = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/{snapshot['id']}"
            )
            assert fetched.status_code == 200
            assert fetched.json()["id"] == snapshot["id"]
            assert fetched.json()["deviations"] == []

            listing = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots"
            )
            assert listing.status_code == 200
            assert [row["id"] for row in listing.json()] == [snapshot["id"]]
            assert listing.json()[0]["deviation_counts"] == {
                "open": 0,
                "confirmed": 0,
                "accepted": 0,
                "discarded": 0,
            }

            # Invalid stage enum → 422 (AC 6)…
            bad_stage = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(stage="penthouse"),
                headers=headers,
            )
            assert bad_stage.status_code == 422
            # …and invalid provenance enum → 422.
            bad_provenance = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(provenance="telepathy"),
                headers=headers,
            )
            assert bad_provenance.status_code == 422


async def test_snapshot_photo_attachments_project_scoped(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_a = await _create_project(client, headers, "A")
            project_b = await _create_project(client, headers, "B")
            for project_id in (project_a, project_b):
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

            asset_id = await _upload_photo(client, headers, project_a)
            attachment_id = await _create_attachment(
                client, headers, project_a, asset_id
            )

            snapshot = await _create_snapshot(
                client, headers, project_a, attachment_ids=[attachment_id]
            )
            assert snapshot["attachment_ids"] == [attachment_id]

            # Foreign-project attachment → 404 (IDOR).
            foreign = await client.post(
                f"/api/v1/projects/{project_b}/progress/snapshots",
                json=_snapshot_payload(attachment_ids=[attachment_id]),
                headers=headers,
            )
            assert foreign.status_code == 404
            assert foreign.json()["detail"]["code"] == "attachment_not_found"

            # Unknown attachment → 404 as well.
            unknown = await client.post(
                f"/api/v1/projects/{project_a}/progress/snapshots",
                json=_snapshot_payload(attachment_ids=[str(uuid4())]),
                headers=headers,
            )
            assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# AC 3: canonical pinning — never re-resolves "latest"


async def test_compare_pins_canonical_revision(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            snapshot = await _create_snapshot(client, headers, project_id)

            # A later canonical command must NOT move the snapshot's pin.
            command = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                headers=headers,
                json={
                    "schema_version": "0.1.0",
                    "command_id": "cmd-after-snapshot",
                    "base_revision_id": revision_id,
                    "operation": "set_color",
                    "target_id": "room.living",
                    "parameters": {"color": "#00ff00"},
                    "origin": "user",
                },
            )
            assert command.status_code == 200
            assert command.json()["revision_id"] != revision_id

            compared = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/compare"
            )
            assert compared.status_code == 200
            body = compared.json()
            assert body["target"] == {
                "kind": "canonical",
                "variant_id": None,
                "revision_id": revision_id,
            }
            assert body["summary"] == {
                "open": 0,
                "confirmed": 0,
                "accepted": 0,
                "discarded": 0,
                "total": 0,
            }
            assert body["snapshot"]["id"] == snapshot["id"]

            # No scene at all → snapshot creation 404s (scene not initialized).
            empty_project = await _create_project(client, headers, "Empty")
            uninitialized = await client.post(
                f"/api/v1/projects/{empty_project}/progress/snapshots",
                json=_snapshot_payload(),
                headers=headers,
            )
            assert uninitialized.status_code == 404
            assert uninitialized.json()["detail"]["code"] == "scene_not_initialized"


# ---------------------------------------------------------------------------
# AC 4: variant compare target must be approved


async def test_snapshot_variant_target_requires_approved(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)

            created = await client.post(
                f"/api/v1/projects/{project_id}/variants:create-from-current",
                json={"title": "Blue"},
                headers=headers,
            )
            assert created.status_code == 201
            variant = created.json()

            # draft → rejected…
            draft = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(
                    compare_target={"kind": "variant", "variant_id": variant["id"]}
                ),
                headers=headers,
            )
            assert draft.status_code == 422
            assert draft.json()["detail"]["code"] == "variant_not_approved"

            # …shortlisted → still rejected…
            await client.patch(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}",
                json={"status": "shortlisted"},
                headers=headers,
            )
            shortlisted = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(
                    compare_target={"kind": "variant", "variant_id": variant["id"]}
                ),
                headers=headers,
            )
            assert shortlisted.status_code == 422

            # …approved pins the variant head.
            await client.patch(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}",
                json={"status": "approved"},
                headers=headers,
            )
            approved = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(
                    compare_target={"kind": "variant", "variant_id": variant["id"]}
                ),
                headers=headers,
            )
            assert approved.status_code == 201, approved.text
            assert approved.json()["compare_target"] == {
                "kind": "variant",
                "variant_id": variant["id"],
                "revision_id": variant["head_scene_revision_id"],
            }

            # A variant of another project → 404 (IDOR).
            other_project, _ = await _setup(client, headers)
            foreign = await client.post(
                f"/api/v1/projects/{other_project}/progress/snapshots",
                json=_snapshot_payload(
                    compare_target={"kind": "variant", "variant_id": variant["id"]}
                ),
                headers=headers,
            )
            assert foreign.status_code == 404


# ---------------------------------------------------------------------------
# AC 5/6/7 + counts: deviation creation


async def test_deviation_creation_dangling_ids_enums_and_counts(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            snapshot = await _create_snapshot(client, headers, project_id)

            async def revision_count() -> int:
                async with app.state.session_factory() as db:
                    return (
                        await db.execute(
                            select(func.count()).select_from(SceneRevisionRow)
                        )
                    ).scalar_one()

            before = await revision_count()

            # Dangling planned entity id + property_path: allowed (AC 5).
            created = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/deviations",
                json=_deviation_payload(
                    planned={
                        "target_type": "entity",
                        "entity_id": "object.ghost",
                        "property_path": "metadata.color",
                        "value": "#ffffff",
                    },
                ),
                headers=headers,
            )
            assert created.status_code == 201, created.text
            deviation = created.json()
            assert deviation["status"] == "open"
            assert deviation["planned_revision_id"] == revision_id
            assert deviation["planned"]["entity_id"] == "object.ghost"
            assert deviation["planned"]["property_path"] == "metadata.color"
            assert deviation["observed"]["kind"] == "property_diff"

            # Enum violations → 422 (AC 6).
            for payload in (
                _deviation_payload(provenance="telepathy"),
                _deviation_payload(confidence="absolute"),
                _deviation_payload(observed={"kind": "hunch", "value": {}}),
                _deviation_payload(planned={"target_type": "galaxy"}),
                _deviation_payload(status="accepted"),
            ):
                rejected = await client.post(
                    f"/api/v1/projects/{project_id}/progress/snapshots/"
                    f"{snapshot['id']}/deviations",
                    json=payload,
                    headers=headers,
                )
                assert rejected.status_code == 422, payload

            # Deviation creation creates NO SceneRevisionRow (AC 7).
            assert await revision_count() == before

            # Snapshot view carries deviations; compare carries summary counts.
            view = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/{snapshot['id']}"
            )
            assert [row["id"] for row in view.json()["deviations"]] == [deviation["id"]]
            compared = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/compare"
            )
            assert compared.status_code == 200
            assert compared.json()["summary"] == {
                "open": 1,
                "confirmed": 0,
                "accepted": 0,
                "discarded": 0,
                "total": 1,
            }


# ---------------------------------------------------------------------------
# AC 8: accept — canonical path, envelope guards, accepted immutability


async def test_accept_canonical_confirmed_deviation(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            snapshot = await _create_snapshot(client, headers, project_id)
            deviation = await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )
            accept_url = (
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{deviation['id']}/accept"
            )

            # Open deviations cannot be accepted → 409.
            early = await client.post(
                accept_url,
                json=_accept_request(deviation["id"], revision_id),
                headers=headers,
            )
            assert early.status_code == 409
            assert early.json()["detail"]["code"] == "deviation_not_confirmed"

            confirmed = await _confirm_deviation(
                client, headers, project_id, deviation["id"]
            )
            assert confirmed["status"] == "confirmed"

            response = await client.post(
                accept_url,
                json=_accept_request(
                    deviation["id"], revision_id, command_id="cmd-accept-canonical"
                ),
                headers=headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            deviation_view = body["deviation"]
            revision = body["revision"]

            assert deviation_view["status"] == "accepted"
            assert deviation_view["accepted_target_kind"] == "canonical"
            assert deviation_view["accepted_variant_id"] is None
            assert deviation_view["accepted_command_id"] == "cmd-accept-canonical"
            assert deviation_view["accepted_revision_id"] == revision["revision_id"]

            # The fix landed as a REAL canonical revision via the command path.
            assert revision["parent_revision_id"] == revision_id
            assert revision["revision_id"] != revision_id
            scene_now = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert scene_now.json()["revision_id"] == revision["revision_id"]

            # Accepted rows are immutable → 409.
            patched = await client.patch(
                f"/api/v1/projects/{project_id}/progress/deviations/{deviation['id']}",
                json={"status": "open"},
                headers=headers,
            )
            assert patched.status_code == 409
            assert patched.json()["detail"]["code"] == "accepted_immutable"

            # Re-accept → 409 (no longer confirmed).
            again = await client.post(
                accept_url,
                json=_accept_request(deviation["id"], revision_id, command_id="cmd-2"),
                headers=headers,
            )
            assert again.status_code == 409

            # Envelope guards on a second confirmed deviation → 422. The
            # canonical head has moved (first accept), so the envelope tests
            # pin the CURRENT head as base.
            second = await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )
            await _confirm_deviation(client, headers, project_id, second["id"])
            second_url = (
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{second['id']}/accept"
            )
            current_head = scene_now.json()["revision_id"]

            wrong_deviation = await client.post(
                second_url,
                json=_accept_request(str(uuid4()), current_head),
                headers=headers,
            )
            assert wrong_deviation.status_code == 422
            assert (
                wrong_deviation.json()["detail"]["code"] == "invalid_accept_envelope"
            )

            # Recursion: an accept wrapping another accept → 422 (never runs).
            inner_accept = _accept_command(
                second["id"], current_head, command_id="command-inner"
            )
            wrapper = _accept_command(
                second["id"], current_head, command_id="command-outer"
            )
            wrapper["parameters"]["change"] = inner_accept
            recursive = await client.post(
                second_url,
                json={"target": {"kind": "canonical"}, "command": wrapper},
                headers=headers,
            )
            assert recursive.status_code == 422
            assert recursive.json()["detail"]["code"] == "invalid_accept_envelope"


# ---------------------------------------------------------------------------
# AC 9: accept — variant path (head moves; stale expected head; archived)


async def test_accept_variant_moves_head_and_guards(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)

            # --- happy path: approved variant head moves -------------------
            variant = await _create_approved_variant(client, headers, project_id)
            variant_head = variant["head_scene_revision_id"]
            snapshot = await _create_snapshot(
                client,
                headers,
                project_id,
                compare_target={"kind": "variant", "variant_id": variant["id"]},
            )
            deviation = await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )
            await _confirm_deviation(client, headers, project_id, deviation["id"])

            accepted = await client.post(
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{deviation['id']}/accept",
                json=_accept_request(
                    deviation["id"],
                    variant_head,
                    target_kind="variant",
                    variant_id=variant["id"],
                    expected_head_revision_id=variant_head,
                    command_id="cmd-accept-variant",
                ),
                headers=headers,
            )
            assert accepted.status_code == 200, accepted.text
            new_head = accepted.json()["revision"]["revision_id"]
            assert new_head != variant_head
            assert accepted.json()["deviation"]["accepted_variant_id"] == variant["id"]
            assert accepted.json()["deviation"]["accepted_revision_id"] == new_head

            variant_now = await client.get(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}"
            )
            assert variant_now.json()["head_scene_revision_id"] == new_head

            # The canonical scene was never touched by the variant accept.
            scene_now = await client.get(f"/api/v1/projects/{project_id}/scene")
            assert scene_now.json()["revision_id"] == revision_id

            # --- stale expected head → 409 ---------------------------------
            stale_snapshot = await _create_snapshot(
                client,
                headers,
                project_id,
                compare_target={"kind": "variant", "variant_id": variant["id"]},
            )
            stale_dev = await _create_deviation(
                client, headers, project_id, stale_snapshot["id"]
            )
            await _confirm_deviation(client, headers, project_id, stale_dev["id"])
            stale = await client.post(
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{stale_dev['id']}/accept",
                json=_accept_request(
                    stale_dev["id"],
                    variant_head,
                    target_kind="variant",
                    variant_id=variant["id"],
                    expected_head_revision_id=variant_head,
                    command_id="cmd-accept-stale",
                ),
                headers=headers,
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "revision_conflict"

            # --- archived variant → 409 ------------------------------------
            archived_variant = await _create_approved_variant(
                client, headers, project_id, title="Archived"
            )
            archived_snapshot = await _create_snapshot(
                client,
                headers,
                project_id,
                compare_target={
                    "kind": "variant",
                    "variant_id": archived_variant["id"],
                },
            )
            archived_dev = await _create_deviation(
                client, headers, project_id, archived_snapshot["id"]
            )
            await _confirm_deviation(client, headers, project_id, archived_dev["id"])
            archived = await client.patch(
                f"/api/v1/projects/{project_id}/variants/{archived_variant['id']}",
                json={"status": "archived"},
                headers=headers,
            )
            assert archived.status_code == 200  # archive itself succeeded
            archived_head = archived_snapshot["compare_target"]["revision_id"]
            archived_accept = await client.post(
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{archived_dev['id']}/accept",
                json=_accept_request(
                    archived_dev["id"],
                    archived_head,
                    target_kind="variant",
                    variant_id=archived_variant["id"],
                    expected_head_revision_id=archived_head,
                    command_id="cmd-accept-archived",
                ),
                headers=headers,
            )
            assert archived_accept.status_code == 409
            assert archived_accept.json()["detail"]["code"] == "variant_archived"

            # Deviation stays confirmed after every rejected accept.
            stale_after = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{stale_snapshot['id']}"
            )
            assert stale_after.json()["deviations"][0]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# AC 10: IDOR — foreign-project snapshot/deviation/variant → 404


async def test_progress_idor_scoping(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_a, revision_a = await _setup(client, headers, "A")
            project_b, revision_b = await _setup(client, headers, "B")

            snapshot_a = await _create_snapshot(client, headers, project_a)
            deviation_a = await _create_deviation(
                client, headers, project_a, snapshot_a["id"]
            )
            variant_b = await _create_approved_variant(client, headers, project_b)

            # Snapshot endpoints scoped by project.
            assert (
                await client.get(
                    f"/api/v1/projects/{project_b}/progress/snapshots/{snapshot_a['id']}"
                )
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/v1/projects/{project_b}/progress/snapshots/"
                    f"{snapshot_a['id']}/compare"
                )
            ).status_code == 404
            assert (
                await client.delete(
                    f"/api/v1/projects/{project_b}/progress/snapshots/{snapshot_a['id']}",
                    headers=headers,
                )
            ).status_code == 404

            # Deviation create on a foreign snapshot → 404 (snapshot scoped).
            assert (
                await client.post(
                    f"/api/v1/projects/{project_b}/progress/snapshots/"
                    f"{snapshot_a['id']}/deviations",
                    json=_deviation_payload(),
                    headers=headers,
                )
            ).status_code == 404

            # Deviation patch/accept under the wrong project → 404.
            assert (
                await client.patch(
                    f"/api/v1/projects/{project_b}/progress/deviations/{deviation_a['id']}",
                    json={"status": "confirmed"},
                    headers=headers,
                )
            ).status_code == 404
            foreign_accept = await client.post(
                f"/api/v1/projects/{project_b}/progress/deviations/"
                f"{deviation_a['id']}/accept",
                json=_accept_request(
                    deviation_a["id"], revision_b, command_id="cmd-idor"
                ),
                headers=headers,
            )
            assert foreign_accept.status_code == 404
            assert foreign_accept.json()["detail"]["code"] == "deviation_not_found"

            # Foreign variant as compare target (covered above) and as accept
            # target → 404 variant_not_found.
            await _confirm_deviation(
                client, headers, project_a, deviation_a["id"]
            )
            foreign_target = await client.post(
                f"/api/v1/projects/{project_a}/progress/deviations/"
                f"{deviation_a['id']}/accept",
                json=_accept_request(
                    deviation_a["id"],
                    revision_a,
                    target_kind="variant",
                    variant_id=variant_b["id"],
                    expected_head_revision_id=variant_b["head_scene_revision_id"],
                    command_id="cmd-idor-variant",
                ),
                headers=headers,
            )
            assert foreign_target.status_code == 404
            assert foreign_target.json()["detail"]["code"] == "variant_not_found"

            # The deviation of project A is untouched by all failed attempts
            # (still confirmed, never accepted).
            still = await client.get(
                f"/api/v1/projects/{project_a}/progress/snapshots/{snapshot_a['id']}"
            )
            assert still.json()["deviations"][0]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# AC 11: PATCH transition table (+ accepted immutability)


async def test_deviation_patch_transition_table(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            snapshot = await _create_snapshot(client, headers, project_id)

            async def new_deviation() -> dict:
                return await _create_deviation(
                    client, headers, project_id, snapshot["id"]
                )

            async def patch(deviation_id: str, body: dict):
                return await client.patch(
                    f"/api/v1/projects/{project_id}/progress/deviations/{deviation_id}",
                    json=body,
                    headers=headers,
                )

            # Full allowed walk: open→confirmed→open→discarded→open→confirmed.
            row = await new_deviation()
            for status in ("confirmed", "open", "discarded", "open", "confirmed"):
                stepped = await patch(row["id"], {"status": status})
                assert stepped.status_code == 200, stepped.text
                assert stepped.json()["status"] == status
            # Allowed side edges: open→discarded, confirmed→discarded.
            edge = await new_deviation()
            assert (await patch(edge["id"], {"status": "discarded"})).json()[
                "status"
            ] == "discarded"

            # Disallowed: discarded→confirmed (must go through open first).
            rejected = await patch(edge["id"], {"status": "confirmed"})
            assert rejected.status_code == 409
            assert rejected.json()["detail"]["code"] == "invalid_status_transition"

            # "accepted" is not patchable at all: the request model's Literal
            # rejects it → 422 (the service-level 409 accepted_not_allowed is
            # defense-in-depth for non-API callers).
            not_allowed = await patch(row["id"], {"status": "accepted"})
            assert not_allowed.status_code == 422

            # Unknown status value → 422.
            unknown = await patch(row["id"], {"status": "vanished"})
            assert unknown.status_code == 422

            # Other patchable fields round-trip.
            enriched = await patch(
                row["id"],
                {
                    "provenance": "manually_measured",
                    "confidence": "high",
                    "observed": {"kind": "note", "value": {"text": "checked twice"}},
                    "note": "verified on site",
                },
            )
            assert enriched.json()["provenance"] == "manually_measured"
            assert enriched.json()["confidence"] == "high"
            assert enriched.json()["observed"] == {
                "kind": "note",
                "value": {"text": "checked twice"},
            }
            assert enriched.json()["note"] == "verified on site"


# ---------------------------------------------------------------------------
# AC 12: soft delete hides from list/get, keeps deviations


async def test_snapshot_soft_delete_hides_but_keeps_deviations(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            asset_id = await _upload_photo(client, headers, project_id)
            attachment_id = await _create_attachment(
                client, headers, project_id, asset_id
            )
            snapshot = await _create_snapshot(
                client, headers, project_id, attachment_ids=[attachment_id]
            )
            await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}/progress/snapshots/{snapshot['id']}",
                headers=headers,
            )
            assert deleted.status_code == 204

            assert (
                await client.get(
                    f"/api/v1/projects/{project_id}/progress/snapshots/{snapshot['id']}"
                )
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/v1/projects/{project_id}/progress/snapshots/"
                    f"{snapshot['id']}/compare"
                )
            ).status_code == 404
            listing = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots"
            )
            assert listing.json() == []
            # include_deleted lifts the hiding.
            kept = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                params={"include_deleted": "true"},
            )
            assert [row["id"] for row in kept.json()] == [snapshot["id"]]
            assert kept.json()[0]["deleted_at"] is not None

            # Attachments and deviations stay untouched.
            attachments = await client.get(
                f"/api/v1/projects/{project_id}/attachments"
            )
            assert [row["id"] for row in attachments.json()] == [attachment_id]
            async with app.state.session_factory() as db:
                deviation_rows = (
                    (await db.execute(select(func.count()).select_from(DeviationRow)))
                ).scalar_one()
            assert deviation_rows == 1


# ---------------------------------------------------------------------------
# AC 13: schema contract for the new operation


def test_design_command_schema_accepts_accept_confirmed_deviation() -> None:
    schema = _design_command_schema()
    envelope = _accept_command("deviation-1", "revision-1", command_id="command-1")
    jsonschema.validate(envelope, schema)


def test_design_command_schema_rejects_embedded_recursion() -> None:
    schema = _design_command_schema()
    # The EMBEDDED change is itself a full accept envelope → recursion.
    inner_accept = _accept_command(
        "deviation-1", "revision-1", command_id="command-inner"
    )
    recursive = _accept_command("deviation-1", "revision-1", command_id="command-outer")
    recursive["parameters"]["change"] = inner_accept
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(recursive, schema)


def test_design_command_schema_accepts_wrapped_plain_change() -> None:
    """A plain (non-accept) embedded change still validates — recursion is
    banned one level deep, wrapping normal operations is the whole point."""
    schema = _design_command_schema()
    envelope = _accept_command("deviation-1", "revision-1", command_id="command-1")
    assert envelope["parameters"]["change"]["operation"] == "set_color"
    jsonschema.validate(envelope, schema)


# ---------------------------------------------------------------------------
# AC 14: compare dangling flag


async def test_compare_marks_planned_ids_dangling(settings) -> None:
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)
            snapshot = await _create_snapshot(client, headers, project_id)

            # Present entity → not dangling; ghost entity → dangling.
            present = await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )
            ghost = await _create_deviation(
                client,
                headers,
                project_id,
                snapshot["id"],
                planned={
                    "target_type": "entity",
                    "entity_id": "object.ghost",
                    "property_path": "metadata.color",
                    "value": "#ffffff",
                },
            )
            room_dev = await _create_deviation(
                client,
                headers,
                project_id,
                snapshot["id"],
                planned={
                    "target_type": "room",
                    "room_id": "room.living",
                },
            )
            ghost_room = await _create_deviation(
                client,
                headers,
                project_id,
                snapshot["id"],
                planned={"target_type": "room", "room_id": "room.narnia"},
            )

            compared = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/compare"
            )
            assert compared.status_code == 200
            flags = {
                row["id"]: row["planned"]["dangling"]
                for row in compared.json()["deviations"]
            }
            assert flags[present["id"]] is False
            assert flags[ghost["id"]] is True
            assert flags[room_dev["id"]] is False
            assert flags[ghost_room["id"]] is True

            # Compare is read-only and deterministic.
            again = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/compare"
            )
            assert again.json() == compared.json()


# ---------------------------------------------------------------------------
# Review fixes: M1 (op restricted to accept endpoint), M3 (compare target
# validation), m5 (observed.value required), m8 (pinned/soft-delete edges)


async def test_generic_command_routes_reject_accept_operation(settings) -> None:
    """M1: accept_confirmed_deviation rides ONLY the deviation accept
    endpoint — both generic command routes reject it with 422 and create
    no revision; the accept endpoint itself keeps working."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)

            async def revision_count() -> int:
                async with app.state.session_factory() as db:
                    return (
                        await db.execute(
                            select(func.count()).select_from(SceneRevisionRow)
                        )
                    ).scalar_one()

            before = await revision_count()

            # Generic canonical route → 422, no revision created.
            canonical = await client.post(
                f"/api/v1/projects/{project_id}/scene/commands",
                json=_accept_command("deviation-1", revision_id, command_id="cmd-x1"),
                headers=headers,
            )
            assert canonical.status_code == 422
            assert canonical.json()["detail"]["code"] == "use_deviation_accept_endpoint"

            # Generic variant route → 422 as well.
            variant = await _create_approved_variant(client, headers, project_id)
            variant_head = (
                await client.get(
                    f"/api/v1/projects/{project_id}/variants/{variant['id']}"
                )
            ).json()["head_scene_revision_id"]
            variant_route = await client.post(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}/revisions",
                json={
                    "expected_head_revision_id": variant_head,
                    "command": _accept_command(
                        "deviation-1", variant_head, command_id="cmd-x2"
                    ),
                },
                headers=headers,
            )
            assert variant_route.status_code == 422
            assert (
                variant_route.json()["detail"]["code"] == "use_deviation_accept_endpoint"
            )
            assert await revision_count() == before

            # The accept endpoint itself still accepts a confirmed deviation.
            snapshot = await _create_snapshot(client, headers, project_id)
            deviation = await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )
            await _confirm_deviation(client, headers, project_id, deviation["id"])
            accepted = await client.post(
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{deviation['id']}/accept",
                json=_accept_request(deviation["id"], revision_id),
                headers=headers,
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["deviation"]["status"] == "accepted"
            assert await revision_count() == before + 1


async def test_snapshot_variant_compare_target_requires_variant_id(settings) -> None:
    """M3: kind=variant without variant_id is rejected at the request model."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, _ = await _setup(client, headers)

            response = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots",
                json=_snapshot_payload(compare_target={"kind": "variant"}),
                headers=headers,
            )
            assert response.status_code == 422


async def test_deviation_observed_value_required(settings) -> None:
    """m5: observed.value=None is a 422 (observed_value_required) and no
    partial deviation row is persisted."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, _ = await _setup(client, headers)
            snapshot = await _create_snapshot(client, headers, project_id)

            async def deviation_count() -> int:
                async with app.state.session_factory() as db:
                    return (
                        await db.execute(
                            select(func.count()).select_from(DeviationRow)
                        )
                    ).scalar_one()

            assert await deviation_count() == 0
            response = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/deviations",
                json=_deviation_payload(
                    observed={"kind": "note", "value": None},
                ),
                headers=headers,
            )
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == "observed_value_required"
            assert await deviation_count() == 0

            # A real value on the same kind is fine (kind is not the problem).
            ok = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{snapshot['id']}/deviations",
                json=_deviation_payload(
                    observed={"kind": "note", "value": "window not installed"},
                ),
                headers=headers,
            )
            assert ok.status_code == 201
            assert await deviation_count() == 1


async def test_progress_pinned_behaviors_on_deleted_targets(settings) -> None:
    """m8: (a) deviations cannot be added to a soft-deleted snapshot;
    (b) compare stays 200 after the variant ROW is deleted (dangling
    variant_id, pinned revision still returned); (c) PINNED BEHAVIOR
    (ruling): accepting a confirmed deviation whose snapshot is soft-deleted
    still succeeds — the snapshot is only the recording context."""
    app = create_app(settings=settings, object_store=MemoryObjectStore())
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await login(client)
            headers = {"X-CSRF-Token": csrf}
            project_id, revision_id = await _setup(client, headers)

            # --- (a) deviation create on soft-deleted snapshot → 404 --------
            deleted_snapshot = await _create_snapshot(client, headers, project_id)
            removed = await client.delete(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{deleted_snapshot['id']}",
                headers=headers,
            )
            assert removed.status_code == 204
            hidden = await client.post(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{deleted_snapshot['id']}/deviations",
                json=_deviation_payload(),
                headers=headers,
            )
            assert hidden.status_code == 404

            # --- (b) compare after the variant ROW is deleted → graceful ----
            variant = await _create_approved_variant(client, headers, project_id)
            variant_snapshot = await _create_snapshot(
                client,
                headers,
                project_id,
                compare_target={
                    "kind": "variant",
                    "variant_id": variant["id"],
                },
            )
            before = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{variant_snapshot['id']}/compare"
            )
            assert before.status_code == 200
            assert (
                before.json()["target"]["revision_id"]
                == variant_snapshot["compare_target"]["revision_id"]
            )

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}/variants/{variant['id']}",
                headers=headers,
            )
            assert deleted.status_code == 204

            after = await client.get(
                f"/api/v1/projects/{project_id}/progress/snapshots/"
                f"{variant_snapshot['id']}/compare"
            )
            assert after.status_code == 200
            # The pinned variant_id survives as a dangling string; the pinned
            # revision is still resolved and returned.
            assert after.json()["target"] == {
                "kind": "variant",
                "variant_id": variant["id"],
                "revision_id": variant_snapshot["compare_target"]["revision_id"],
            }
            assert after.json()["snapshot"]["id"] == variant_snapshot["id"]

            # --- (c) accept with soft-deleted snapshot → 200 (pinned) -------
            snapshot = await _create_snapshot(client, headers, project_id)
            deviation = await _create_deviation(
                client, headers, project_id, snapshot["id"]
            )
            await _confirm_deviation(client, headers, project_id, deviation["id"])
            removed_snapshot = await client.delete(
                f"/api/v1/projects/{project_id}/progress/snapshots/{snapshot['id']}",
                headers=headers,
            )
            assert removed_snapshot.status_code == 204

            accepted = await client.post(
                f"/api/v1/projects/{project_id}/progress/deviations/"
                f"{deviation['id']}/accept",
                json=_accept_request(deviation["id"], revision_id),
                headers=headers,
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["deviation"]["status"] == "accepted"
            assert (
                accepted.json()["revision"]["revision_id"] != revision_id
            )  # a NEW canonical revision was created
