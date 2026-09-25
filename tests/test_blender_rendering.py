import json
from pathlib import Path

import jsonschema
import pytest

from stroy.domain.models import Scene
from stroy.rendering import (
    BlenderAdapter,
    RenderContext,
    build_blender_plan,
    finalize_render_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def golden_scene() -> Scene:
    return Scene.model_validate_json(
        (ROOT / "fixtures" / "golden-room.scene.json").read_text(encoding="utf-8")
    )


def expected() -> dict:
    return json.loads(
        (ROOT / "fixtures" / "golden-room.expected.json").read_text(
            encoding="utf-8"
        )
    )


def test_golden_room_plan_is_deterministic_and_metric() -> None:
    scene = golden_scene()
    first = build_blender_plan(
        scene,
        scene_revision_id="revision.golden-room",
        camera_id="camera.living.entry",
    )
    second = build_blender_plan(
        scene,
        scene_revision_id="revision.golden-room",
        camera_id="camera.living.entry",
    )

    assert first.canonical_json() == second.canonical_json()
    fixture = expected()
    assert [entity.semantic_id for entity in first.entities] == fixture[
        "renderable_semantic_ids"
    ]

    floor = next(
        entity
        for entity in first.entities
        if entity.semantic_id == "surface.floor.living"
    )
    assert list(floor.dimensions_m or ()) == fixture["floor_dimensions_m"]
    assert list(floor.translation_m) == fixture["floor_translation_m"]
    assert first.passes == fixture["passes"]


def test_object_and_material_indices_are_stable_and_nonzero() -> None:
    scene = golden_scene()
    plan = build_blender_plan(
        scene,
        scene_revision_id="revision.golden-room",
        camera_id="camera.living.entry",
    )
    object_indices = {entity.semantic_id: entity.object_index for entity in plan.entities}
    material_indices = {
        entity.material_ref: entity.material_index for entity in plan.entities
    }

    assert all(value > 0 for value in object_indices.values())
    assert all(value > 0 for value in material_indices.values())
    assert len(object_indices.values()) == len(set(object_indices.values()))
    assert len(material_indices.values()) == len(set(material_indices.values()))

    rerun = build_blender_plan(
        scene,
        scene_revision_id="revision.golden-room",
        camera_id="camera.living.entry",
    )
    assert object_indices == {
        entity.semantic_id: entity.object_index for entity in rerun.entities
    }
    assert material_indices == {
        entity.material_ref: entity.material_index for entity in rerun.entities
    }


def test_camera_intrinsics_translate_without_hidden_resolution_change() -> None:
    plan = build_blender_plan(
        golden_scene(),
        scene_revision_id="revision.golden-room",
        camera_id="camera.living.entry",
    )
    camera = plan.camera
    assert [camera.width_px, camera.height_px] == expected()["resolution"]
    assert camera.lens_mm == pytest.approx(36.0 * 1200.0 / 1600.0)
    assert camera.pixel_aspect_y == pytest.approx(1210.0 / 1200.0)
    assert camera.shift_x == pytest.approx((800.0 - 790.0) / 1600.0)
    assert camera.shift_y == pytest.approx(
        (505.0 - 500.0) * (1210.0 / 1200.0) / 1600.0
    )


def test_blender_command_is_headless_and_explicit() -> None:
    adapter = BlenderAdapter(
        "/opt/blender/blender",
        script_path=ROOT / "blender" / "stroy_blender.py",
    )
    command = adapter.command(
        plan_path="/tmp/plan.json",
        output_dir="/tmp/render",
        render=True,
    )
    assert command[:3] == [
        "/opt/blender/blender",
        "--background",
        "--factory-startup",
    ]
    assert "--python" in command
    assert "--render" in command
    assert "/tmp/plan.json" in command
    assert "/tmp/render" in command


def test_blender_script_compiles_without_importing_bpy() -> None:
    source = (ROOT / "blender" / "stroy_blender.py").read_text(encoding="utf-8")
    compile(source, "stroy_blender.py", "exec")


def test_render_manifest_matches_versioned_schema() -> None:
    manifest = finalize_render_manifest(
        context=RenderContext(
            render_id="render-1",
            scene_revision_id="revision.golden-room",
            camera_id="camera.living.entry",
        ),
        pass_asset_ids={
            "rgb": "asset-rgb",
            "depth": "asset-depth",
            "normals": "asset-normals",
            "object_ids": "asset-object",
            "material_ids": "asset-material",
        },
    )
    schema = json.loads(
        (ROOT / "schemas" / "render-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(
        manifest.model_dump(mode="json", exclude_none=True),
        schema,
    )


def test_render_manifest_rejects_missing_aligned_pass() -> None:
    with pytest.raises(ValueError, match="missing passes"):
        finalize_render_manifest(
            context=RenderContext(
                render_id="render-1",
                scene_revision_id="revision.golden-room",
                camera_id="camera.living.entry",
            ),
            pass_asset_ids={
                "rgb": "asset-rgb",
                "depth": "asset-depth",
                "normals": "asset-normals",
                "object_ids": "asset-object",
            },
        )
