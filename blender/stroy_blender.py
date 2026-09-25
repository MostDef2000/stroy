from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import bpy


def _args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--render", action="store_true")
    return parser.parse_args(argv)


def _reset() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.lights,
    ):
        for datablock in list(collection):
            collection.remove(datablock)


def _engine(scene) -> str:
    # Object/Material Index passes are required control outputs and are
    # consistently available in Cycles. CPU fallback works headlessly.
    scene.render.engine = "CYCLES"
    if hasattr(scene, "cycles"):
        scene.cycles.device = "CPU"
        scene.cycles.samples = int(os.getenv("STROY_BLENDER_SAMPLES", "16"))
        scene.cycles.use_denoising = False
    return "CYCLES"


def _material(entity: dict):
    name = entity["material_ref"]
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    material = bpy.data.materials.new(name=name)
    material.diffuse_color = tuple(entity["color_rgba"])
    material.pass_index = int(entity["material_index"])
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = tuple(entity["color_rgba"])
        principled.inputs["Roughness"].default_value = 0.55
    material["stroy_material_ref"] = name
    material["stroy_material_index"] = int(entity["material_index"])
    return material


def _entity(entity: dict):
    semantic_id = entity["semantic_id"]
    kind = entity["kind"]
    location = tuple(entity["translation_m"])
    rotation = tuple(entity["rotation_rad"])
    dimensions = entity.get("dimensions_m")

    if kind == "light" and dimensions is None:
        data = bpy.data.lights.new(name=semantic_id, type="AREA")
        data.energy = float(entity.get("metadata", {}).get("power_w", 500.0))
        data.shape = "DISK"
        data.size = float(entity.get("metadata", {}).get("size_m", 2.0))
        obj = bpy.data.objects.new(semantic_id, data)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = rotation
    elif dimensions is not None:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=location, rotation=rotation)
        obj = bpy.context.active_object
        obj.name = semantic_id
        obj.dimensions = tuple(dimensions)
        obj.scale = tuple(
            obj.scale[index] * float(entity["scale"][index])
            for index in range(3)
        )
        obj.data.materials.append(_material(entity))
    else:
        obj = bpy.data.objects.new(semantic_id, None)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = rotation
        obj.scale = tuple(entity["scale"])

    obj.pass_index = int(entity["object_index"])
    obj["stroy_id"] = semantic_id
    obj["stroy_kind"] = kind
    obj["stroy_object_index"] = int(entity["object_index"])
    obj["stroy_material_ref"] = entity["material_ref"]
    return obj


def _camera(scene, plan: dict):
    data = bpy.data.cameras.new(name=plan["semantic_id"])
    data.type = "PERSP"
    data.sensor_fit = "HORIZONTAL"
    data.sensor_width = float(plan["sensor_width_mm"])
    data.lens = float(plan["lens_mm"])
    data.shift_x = float(plan["shift_x"])
    data.shift_y = float(plan["shift_y"])
    data.clip_start = float(plan.get("clip_start_m", 0.01))
    data.clip_end = float(plan.get("clip_end_m", 1000.0))

    obj = bpy.data.objects.new(plan["semantic_id"], data)
    bpy.context.collection.objects.link(obj)
    obj.location = tuple(plan["translation_m"])
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = tuple(plan["rotation_rad"])
    obj["stroy_id"] = plan["semantic_id"]
    obj["stroy_fx"] = float(plan["fx"])
    obj["stroy_fy"] = float(plan["fy"])
    obj["stroy_cx"] = float(plan["cx"])
    obj["stroy_cy"] = float(plan["cy"])

    scene.camera = obj
    scene.render.resolution_x = int(plan["width_px"])
    scene.render.resolution_y = int(plan["height_px"])
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = float(plan["pixel_aspect_x"])
    scene.render.pixel_aspect_y = float(plan["pixel_aspect_y"])
    return obj


def _file_output(nodes, links, source_socket, output_dir: Path, prefix: str, *, color_mode: str):
    node = nodes.new("CompositorNodeOutputFile")
    node.base_path = str(output_dir)
    node.format.file_format = "OPEN_EXR"
    node.format.color_depth = "32"
    node.format.color_mode = color_mode
    node.file_slots[0].path = prefix + "_"
    links.new(source_socket, node.inputs[0])


def _setup_passes(scene, output_dir: Path) -> None:
    layer = scene.view_layers[0]
    layer.use_pass_z = True
    layer.use_pass_normal = True
    layer.use_pass_object_index = True
    layer.use_pass_material_index = True

    scene.use_nodes = True
    tree = scene.node_tree
    tree.nodes.clear()
    render_layers = tree.nodes.new("CompositorNodeRLayers")
    composite = tree.nodes.new("CompositorNodeComposite")
    tree.links.new(render_layers.outputs["Image"], composite.inputs["Image"])

    _file_output(
        tree.nodes,
        tree.links,
        render_layers.outputs["Depth"],
        output_dir,
        "depth",
        color_mode="RGB",
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers.outputs["Normal"],
        output_dir,
        "normals",
        color_mode="RGB",
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers.outputs["IndexOB"],
        output_dir,
        "object_ids",
        color_mode="RGB",
    )
    _file_output(
        tree.nodes,
        tree.links,
        render_layers.outputs["IndexMA"],
        output_dir,
        "material_ids",
        color_mode="RGB",
    )


def _normalize_output(output_dir: Path, prefix: str) -> None:
    candidates = sorted(output_dir.glob(prefix + "_*.exr"))
    if not candidates:
        candidates = sorted(output_dir.glob(prefix + "*.exr"))
    if not candidates:
        raise RuntimeError(f"missing compositor output for {prefix}")
    target = output_dir / f"{prefix}.exr"
    candidates[0].replace(target)
    for extra in candidates[1:]:
        extra.unlink(missing_ok=True)


def _metadata(plan: dict, engine: str) -> dict:
    return {
        "schema_version": "0.1.0",
        "scene_revision_id": plan["scene_revision_id"],
        "design_revision_id": plan.get("design_revision_id"),
        "scene_id": plan["scene_id"],
        "renderer_profile": plan["renderer_profile"],
        "engine": engine,
        "camera": plan["camera"],
        "objects": [
            {
                "semantic_id": entity["semantic_id"],
                "kind": entity["kind"],
                "translation_m": entity["translation_m"],
                "rotation_rad": entity["rotation_rad"],
                "scale": entity["scale"],
                "dimensions_m": entity.get("dimensions_m"),
                "material_ref": entity["material_ref"],
                "object_index": entity["object_index"],
                "material_index": entity["material_index"],
            }
            for entity in plan["entities"]
        ],
        "passes": list(plan["passes"]),
    }


def main() -> int:
    args = _args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    _reset()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    engine = _engine(scene)
    scene.render.film_transparent = False
    scene.world.color = (0.055, 0.055, 0.055)

    for entity in plan["entities"]:
        _entity(entity)
    _camera(scene, plan["camera"])

    if args.render:
        _setup_passes(scene, output_dir)
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.render.filepath = str(output_dir / "rgb.png")
        bpy.ops.render.render(write_still=True)
        for name in ("depth", "normals", "object_ids", "material_ids"):
            _normalize_output(output_dir, name)

    metadata = _metadata(plan, engine)
    (output_dir / "scene_metadata.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
