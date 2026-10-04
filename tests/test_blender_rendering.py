import importlib.util
import json
import math
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
BENCH_SCENE = ROOT / "fixtures" / "bare-twin-bench.scene.json"


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


def bare_twin_scene() -> Scene:
    return Scene.model_validate_json(BENCH_SCENE.read_text(encoding="utf-8"))


def test_bare_twin_bench_fixture_validates() -> None:
    scene = bare_twin_scene()
    assert len(scene.cameras) >= 3
    assert scene.metadata.get("synthetic") is True
    assert [camera.id for camera in scene.cameras] == [
        "camera.entry",
        "camera.living.diagonal",
        "camera.kitchen.corridor",
    ]


def test_bare_twin_bench_geometry_sane() -> None:
    scene = bare_twin_scene()
    walls = [entity for entity in scene.entities if entity.kind.value == "wall"]
    doors = [entity for entity in scene.entities if entity.kind.value == "door"]
    windows = [entity for entity in scene.entities if entity.kind.value == "window"]

    assert len(walls) >= 12
    assert len(doors) >= 5
    assert len(windows) >= 2

    for entity in scene.entities:
        for value in entity.transform.translation_mm:
            assert value is not None
            assert math.isfinite(value)
        dimensions = entity.geometry.get("dimensions_mm")
        if dimensions is not None:
            assert len(dimensions) == 3
            assert all(value > 0 for value in dimensions)


def load_blender_script_module():
    spec = importlib.util.spec_from_file_location(
        "stroy_blender_under_test",
        ROOT / "blender" / "stroy_blender.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_renderer_profiles_known() -> None:
    module = load_blender_script_module()
    profiles = module.RENDERER_PROFILES

    assert set(profiles) >= {
        "blender-eevee-v0",
        "blender-cycles-v0",
        "blender-cycles-gpu-v0",
    }
    assert profiles["blender-eevee-v0"]["samples"] == 16
    assert profiles["blender-eevee-v0"]["denoise"] is False
    assert profiles["blender-cycles-v0"]["engine"] == "CYCLES"
    assert profiles["blender-cycles-v0"]["device"] == "CPU"
    assert profiles["blender-cycles-v0"]["samples"] == 16
    assert profiles["blender-cycles-gpu-v0"]["engine"] == "CYCLES"
    assert profiles["blender-cycles-gpu-v0"]["device"] == "GPU"
    assert profiles["blender-cycles-gpu-v0"]["samples"] == 32
    assert profiles["blender-cycles-gpu-v0"]["denoise"] is True

    assert module.resolve_renderer_profile("blender-cycles-v0")["engine"] == "CYCLES"
    assert module.resolve_renderer_profile(None) == profiles["blender-cycles-v0"]
    with pytest.raises(RuntimeError, match="unknown renderer_profile"):
        module.resolve_renderer_profile("blender-unknown-v0")


def test_benchmark_script_compiles() -> None:
    source = (ROOT / "scripts" / "benchmark_bare_twin.py").read_text(encoding="utf-8")
    compile(source, "benchmark_bare_twin.py", "exec")


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
