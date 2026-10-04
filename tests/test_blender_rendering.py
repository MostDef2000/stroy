import importlib.util
import json
import math
import types
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

    # A crashing --python script must fail the process instead of reporting 0.
    exit_code_index = command.index("--python-exit-code")
    assert command[exit_code_index + 1] == "1"
    # Blender global options must precede the "--" script-arg separator.
    assert exit_code_index < command.index("--")
    assert exit_code_index < command.index("--python")


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


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "benchmark_bare_twin_under_test",
        ROOT / "scripts" / "benchmark_bare_twin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_blender_plan() -> dict:
    return {
        "scene_revision_id": "revision.test",
        "design_revision_id": None,
        "scene_id": "scene.test",
        "renderer_profile": "blender-cycles-v0",
        "camera": {"semantic_id": "camera.test"},
        "entities": [],
        "passes": ["rgb"],
    }


def test_metadata_omits_blender_version_without_bpy() -> None:
    module = load_blender_script_module()
    assert module.bpy is None
    metadata = module._metadata(minimal_blender_plan(), "CYCLES")
    assert "blender_version" not in metadata


def test_metadata_includes_blender_version_with_bpy(monkeypatch) -> None:
    module = load_blender_script_module()
    fake_app = type("app", (), {"version_string": "5.0.1"})
    monkeypatch.setattr(module, "bpy", type("FakeBpy", (), {"app": fake_app}))
    metadata = module._metadata(minimal_blender_plan(), "CYCLES")
    assert metadata["blender_version"] == "5.0.1"


def test_pick_compute_device_type_matches_enum_items() -> None:
    module = load_blender_script_module()
    enum_items = [
        types.SimpleNamespace(identifier="OPTIX"),
        types.SimpleNamespace(identifier="CUDA"),
    ]
    stub = types.SimpleNamespace(
        bl_rna=types.SimpleNamespace(
            properties={
                "compute_device_type": types.SimpleNamespace(enum_items=enum_items)
            }
        )
    )
    assert module._pick_compute_device_type(stub, ("OPTIX", "CUDA")) == "OPTIX"
    assert module._pick_compute_device_type(stub, ("HIP",)) == "NONE"


def test_pick_compute_device_type_without_rna_returns_none() -> None:
    module = load_blender_script_module()
    assert module._pick_compute_device_type(object(), ("OPTIX",)) == "NONE"


def test_compositing_tree_uses_legacy_scene_node_tree() -> None:
    module = load_blender_script_module()
    legacy_tree = object()
    scene = types.SimpleNamespace(node_tree=legacy_tree, use_nodes=False)

    assert module._compositing_tree(scene) is legacy_tree
    assert scene.use_nodes is True


def test_compositing_tree_creates_node_group_on_blender_5(monkeypatch) -> None:
    module = load_blender_script_module()
    created: list[tuple[str, str]] = []
    group = types.SimpleNamespace(nodes=object(), links=object())

    class FakeNodeGroups:
        def new(self, name: str, type_name: str):
            created.append((name, type_name))
            return group

    fake_bpy = types.SimpleNamespace(
        data=types.SimpleNamespace(node_groups=FakeNodeGroups())
    )
    monkeypatch.setattr(module, "bpy", fake_bpy)
    scene = types.SimpleNamespace(use_nodes=False, compositing_node_group=None)

    tree = module._compositing_tree(scene)

    assert tree is group
    assert scene.compositing_node_group is group
    assert created == [("stroy-compositor", "CompositorNodeTree")]
    assert scene.use_nodes is True


def test_compositing_tree_raises_without_compositor_api() -> None:
    module = load_blender_script_module()
    with pytest.raises(RuntimeError, match="no compositing tree API"):
        module._compositing_tree(types.SimpleNamespace())


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


def test_benchmark_report_marks_nonzero_return_code_as_failure(
    tmp_path: Path,
) -> None:
    module = load_benchmark_module()
    run = {
        "profile": "blender-cycles-v0",
        "camera_id": "camera.entry",
        "run_index": 0,
        "warm": False,
        "wall_seconds": 2.2,
        "peak_vram_mib": None,
        "return_code": 1,
        "output_dir": str(tmp_path / "run-0"),
    }
    module.write_outputs(
        tmp_path,
        scene="fixtures/bare-twin-bench.scene.json",
        resolution=1024,
        blender_bin="blender",
        blender_version="5.0.1",
        profiles=["blender-cycles-v0"],
        runs=[run],
        warnings=["blender-cycles-v0/camera.entry/run-0 exited 1"],
        runs_per=1,
    )

    report = (tmp_path / "benchmark.md").read_text(encoding="utf-8")
    assert "| rc=1 |" in report
    assert "| ok |" not in report
    assert "- Blender version: 5.0.1" in report
    payload = json.loads((tmp_path / "benchmark.json").read_text(encoding="utf-8"))
    assert payload["runs"][0]["return_code"] == 1
    assert payload["blender_version"] == "5.0.1"


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


def test_declare_group_output_socket_uses_interface_new_socket() -> None:
    module = load_blender_script_module()
    calls: list[tuple[str, str, str]] = []

    class FakeInterface:
        def new_socket(self, *, name: str, in_out: str, socket_type: str):
            calls.append((name, in_out, socket_type))
            return types.SimpleNamespace(name=name)

    tree = types.SimpleNamespace(interface=FakeInterface())
    assert (
        module._declare_group_output_socket(tree, "Image", "NodeSocketColor") == "Image"
    )
    assert calls == [("Image", "OUTPUT", "NodeSocketColor")]


def test_declare_group_output_socket_falls_back_to_outputs_new() -> None:
    module = load_blender_script_module()
    calls: list[tuple[str, str]] = []

    class FakeOutputs:
        def new(self, name: str, socket_type: str):
            calls.append((name, socket_type))
            return types.SimpleNamespace(name=name)

    tree = types.SimpleNamespace(outputs=FakeOutputs())
    assert (
        module._declare_group_output_socket(tree, "Image", "NodeSocketColor") == "Image"
    )
    assert calls == [("Image", "NodeSocketColor")]


def test_declare_group_output_socket_rejects_bad_socket_type() -> None:
    module = load_blender_script_module()

    class FakeInterface:
        def new_socket(self, *, name: str, in_out: str, socket_type: str):
            raise TypeError(f"unknown socket type: {socket_type}")

    with pytest.raises(RuntimeError, match="NodeSocketColor"):
        module._declare_group_output_socket(
            types.SimpleNamespace(interface=FakeInterface()), "Image", "NotASocket"
        )


def test_declare_group_output_socket_raises_when_no_api() -> None:
    module = load_blender_script_module()
    with pytest.raises(RuntimeError, match="available attributes"):
        module._declare_group_output_socket(
            types.SimpleNamespace(alpha=1), "Image", "NodeSocketColor"
        )


def test_is_compositor_group_discriminates_scene_trees() -> None:
    module = load_blender_script_module()
    group = object()
    assert module._is_compositor_group(types.SimpleNamespace(), group) is False
    assert module._is_compositor_group(types.SimpleNamespace(), None) is False
    scene = types.SimpleNamespace(compositing_node_group=group)
    assert module._is_compositor_group(scene, group) is True
    assert module._is_compositor_group(scene, object()) is False


def file_output_stub_harness(module, *, with_items: bool):
    """Run ``_file_output`` against a fake node and record what it touched."""
    record: dict = {"items": [], "links": [], "nodes": [], "source": None}

    class FakeItems:
        def __init__(self, node):
            self._node = node

        def new(self, socket_type: str, name: str):
            record["items"].append((socket_type, name))
            self._node.inputs[name] = ("input", name)

    class FakeNode:
        def __init__(self):
            self.format = types.SimpleNamespace()
            if with_items:
                self.inputs: dict = {}
                self.file_output_items = FakeItems(self)
            else:
                self.inputs = {0: ("slot", 0)}
                self.base_path = None
                self.file_slots = [types.SimpleNamespace(path=None)]

    class FakeNodes:
        def new(self, type_name: str):
            assert type_name == "CompositorNodeOutputFile"
            node = FakeNode()
            record["nodes"].append(node)
            return node

    class FakeLinks:
        def new(self, source, target):
            record["links"].append((source, target))

    source = object()
    record["source"] = source
    module._file_output(
        FakeNodes(),
        FakeLinks(),
        source,
        Path("/tmp/out"),
        "depth",
        color_mode="RGB",
    )
    return record


def test_uses_file_output_items_detects_blender_5_api() -> None:
    module = load_blender_script_module()
    assert module._uses_file_output_items(
        types.SimpleNamespace(file_output_items=object())
    )
    assert not module._uses_file_output_items(
        types.SimpleNamespace(base_path="/tmp", file_slots=[])
    )
    assert not module._uses_file_output_items(types.SimpleNamespace())


def test_file_output_5_api_sets_directory_file_name_and_item() -> None:
    module = load_blender_script_module()
    record = file_output_stub_harness(module, with_items=True)
    node = record["nodes"][0]

    assert node.directory == "/tmp/out"
    assert node.file_name == "depth_"
    assert record["items"] == [("RGBA", "depth")]
    assert record["links"] == [(record["source"], ("input", "depth"))]
    # 5.0 must not touch the removed legacy attributes / node format enum.
    assert not hasattr(node, "base_path")
    assert not hasattr(node, "file_slots")
    assert not hasattr(node.format, "file_format")


def test_file_output_legacy_api_uses_base_path_and_file_slots() -> None:
    module = load_blender_script_module()
    record = file_output_stub_harness(module, with_items=False)
    node = record["nodes"][0]

    assert node.base_path == "/tmp/out"
    assert node.file_slots[0].path == "depth_"
    assert node.format.file_format == "OPEN_EXR"
    assert node.format.color_depth == "32"
    assert node.format.color_mode == "RGB"
    assert record["items"] == []
    assert record["links"] == [(record["source"], ("slot", 0))]


def test_parse_blender_version_extracts_token() -> None:
    module = load_benchmark_module()
    assert module.parse_blender_version("Blender 5.0.1") == "5.0.1"
    assert module.parse_blender_version("Blender 4.2") == "4.2"
    assert module.parse_blender_version("no version here") is None
