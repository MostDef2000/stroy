from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:  # Blender's bundled Python provides bpy; the guard keeps the module testable.
    import bpy
except ImportError:  # pragma: no cover - only outside Blender
    bpy = None  # type: ignore[assignment]


DEFAULT_RENDERER_PROFILE = "blender-cycles-v0"

# The plan carries only the requested profile name, and this script is executed
# by Blender's bundled Python (no STROY/third-party packages available), so the
# registry lives here and must stay import-free. Profiles are explicit; the
# fallback (no profile in the plan) reproduces the original hardcoded CPU Cycles
# behaviour so existing callers are unaffected.
RENDERER_PROFILES: dict[str, dict[str, object]] = {
    "blender-eevee-v0": {
        "engine": "EEVEE",
        "engine_ids": ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"),
        "samples": 16,
        "denoise": False,
    },
    "blender-cycles-v0": {
        "engine": "CYCLES",
        "device": "CPU",
        "samples": 16,
        "denoise": False,
    },
    "blender-cycles-gpu-v0": {
        "engine": "CYCLES",
        "device": "GPU",
        "compute_device_type": ("OPTIX", "CUDA"),
        "samples": 32,
        "denoise": True,
    },
}


def resolve_renderer_profile(profile: str | None = None) -> dict[str, object]:
    """Return settings for *profile* or raise RuntimeError when unknown."""
    name = profile or DEFAULT_RENDERER_PROFILE
    try:
        return RENDERER_PROFILES[name]
    except KeyError:
        raise RuntimeError(f"unknown renderer_profile: {profile}") from None


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


def _enum_items(datablock, property_name: str) -> set[str]:
    try:
        return {
            item.identifier
            for item in datablock.bl_rna.properties[property_name].enum_items
        }
    except (AttributeError, KeyError, TypeError):
        return set()


def _pick_engine(scene, candidates: tuple[str, ...]) -> str:
    available = _enum_items(scene.render, "engine")
    if available:
        for candidate in candidates:
            if candidate in available:
                return candidate
    return candidates[-1]


def _pick_compute_device_type(datablock, candidates: tuple[str, ...]) -> str:
    available = _enum_items(datablock, "compute_device_type")
    for candidate in candidates:
        if candidate in available:
            return candidate
    return "NONE"


def _cycles_preferences():
    """Return the Cycles addon preferences, or None when unavailable."""
    try:
        addon = bpy.context.preferences.addons.get("cycles")
    except AttributeError:
        return None
    if addon is None:
        return None
    return addon.preferences


def _refresh_devices(preferences) -> None:
    """Populate ``preferences.devices`` across Blender versions.

    Blender 5.0 renamed ``get_devices`` to ``refresh_devices``; prefer the new
    name and fall back to the legacy one.
    """
    for name in ("refresh_devices", "get_devices"):
        refresh = getattr(preferences, name, None)
        if callable(refresh):
            refresh()
            return


def _enable_all_devices() -> None:
    preferences = _cycles_preferences()
    if preferences is None:
        return
    _refresh_devices(preferences)
    for device in preferences.devices:
        device.use = True


def _engine(scene, profile: str | None = None) -> str:
    config = resolve_renderer_profile(profile)
    engine_kind = str(config["engine"])
    samples = int(os.getenv("STROY_BLENDER_SAMPLES", str(config["samples"])))

    if engine_kind == "EEVEE":
        engine = _pick_engine(scene, config["engine_ids"])  # type: ignore[arg-type]
        scene.render.engine = engine
        eevee = getattr(scene, "eevee", None)
        if eevee is not None and hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = samples
        return engine

    if engine_kind == "CYCLES":
        # Object/Material Index passes are required control outputs and are
        # consistently available in Cycles.
        scene.render.engine = "CYCLES"
        cycles = getattr(scene, "cycles", None)
        if cycles is not None:
            cycles.device = str(config.get("device", "CPU"))
            cycles.samples = samples
            cycles.use_denoising = bool(config.get("denoise", False))
            if config.get("device") == "GPU":
                # Blender 5.0 removed ``compute_device_type`` from
                # scene.cycles; it lives on the Cycles addon preferences, which
                # exposes the same enum on 4.x and 5.0.
                preferences = _cycles_preferences()
                if preferences is not None:
                    _refresh_devices(preferences)
                    preferences.compute_device_type = _pick_compute_device_type(
                        preferences,
                        config["compute_device_type"],  # type: ignore[arg-type]
                    )
                    _enable_all_devices()
                cycles.device = "GPU"
        return "CYCLES"

    raise RuntimeError(f"unknown renderer engine: {engine_kind}")


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


def _uses_file_output_items(node) -> bool:
    """True when *node* exposes the Blender 5.0 file-output API.

    Blender 5.0 removed ``base_path``/``file_slots`` from
    ``CompositorNodeOutputFile`` in favour of ``directory``/``file_name`` plus a
    ``file_output_items`` collection. Probing the created node mirrors the
    ``hasattr`` version detection used for the compositing tree and avoids
    depending on a version string.
    """
    return hasattr(node, "file_output_items")


def _file_output(nodes, links, source_socket, output_dir: Path, prefix: str, *, color_mode: str):
    node = nodes.new("CompositorNodeOutputFile")
    if _uses_file_output_items(node):
        # Blender 5.0: base_path/file_slots are gone. ``directory`` + ``file_name``
        # replace base_path + slot path, and one named item supplies the input
        # socket. The node's format enum only offers OPEN_EXR_MULTILAYER, so the
        # pass files are written as EXR; do NOT force PNG here (rgb.png is still
        # written through scene.render, which does support PNG).
        #
        # Naming: 4.x wrote ``<output_dir>/<prefix>_<frame>.exr`` from
        # base_path + the slot path ``<prefix>_``. 5.0 writes ``<output_dir>/
        # <file_name>...``; keeping ``file_name`` = ``<prefix>_`` and the item
        # named ``<prefix>`` leaves the ``<prefix>`` stem intact, so
        # ``_normalize_output`` (``<prefix>_*.exr`` / ``<prefix>*.exr``) still
        # normalises the pass artifact to ``<prefix>.exr``.
        node.directory = str(output_dir)
        node.file_name = prefix + "_"
        node.file_output_items.new("RGBA", prefix)
        links.new(source_socket, node.inputs[prefix])
        return

    # Blender 4.x legacy compositor tree: base_path + file_slots (unchanged).
    node.base_path = str(output_dir)
    node.format.file_format = "OPEN_EXR"
    node.format.color_depth = "32"
    node.format.color_mode = color_mode
    node.file_slots[0].path = prefix + "_"
    links.new(source_socket, node.inputs[0])


def _compositing_tree(scene):
    """Return the scene compositing node tree across Blender versions.

    Blender 4.x materialises ``scene.node_tree`` by enabling ``scene.use_nodes``.
    Blender 5.0 removed ``scene.node_tree`` in favour of
    ``scene.compositing_node_group``, which starts out ``None`` at factory
    startup and must be assigned a freshly created ``CompositorNodeTree``.
    """
    if hasattr(scene, "node_tree"):
        if hasattr(scene, "use_nodes"):
            scene.use_nodes = True
        tree = scene.node_tree
        if tree is not None:
            return tree
    if hasattr(scene, "compositing_node_group"):
        if hasattr(scene, "use_nodes"):
            # Still accepted on 5.0 (deprecated, removed in 6.0); setting it
            # keeps the 4.x habit while 6.0 will simply skip this line.
            scene.use_nodes = True
        tree = bpy.data.node_groups.new("stroy-compositor", "CompositorNodeTree")
        scene.compositing_node_group = tree
        return tree
    raise RuntimeError("unsupported Blender: no compositing tree API")


def _declare_group_output_socket(tree, name: str, socket_type: str) -> str:
    """Declare an output socket on a compositor node group and return its name.

    Two API generations exist. Blender 5.0 exposes
    ``tree.interface.new_socket(name=..., in_out='OUTPUT', socket_type=...)``;
    the legacy node-group API is ``tree.outputs.new(name, socket_type)``. The
    type must be a valid ``NodeSocket`` identifier (RGBA colour is
    ``"NodeSocketColor"``). Errors are surfaced with the original message
    instead of being swallowed.
    """
    interface = getattr(tree, "interface", None)
    if interface is not None and hasattr(interface, "new_socket"):
        try:
            socket = interface.new_socket(
                name=name, in_out="OUTPUT", socket_type=socket_type
            )
        except Exception as exc:  # noqa: BLE001 - surface any Blender rejection
            raise RuntimeError(
                f"tree.interface.new_socket(name={name!r}, in_out='OUTPUT', "
                f"socket_type={socket_type!r}) failed: {exc}. Expected a valid "
                "NodeSocket identifier such as 'NodeSocketColor'."
            ) from exc
        return getattr(socket, "name", name)

    outputs = getattr(tree, "outputs", None)
    if outputs is not None and hasattr(outputs, "new"):
        try:
            socket = outputs.new(name, socket_type)
        except Exception as exc:  # noqa: BLE001 - surface any Blender rejection
            raise RuntimeError(
                f"tree.outputs.new({name!r}, {socket_type!r}) failed: {exc}. "
                "Expected a valid NodeSocket identifier such as 'NodeSocketColor'."
            ) from exc
        return getattr(socket, "name", name)

    available = sorted(attr for attr in dir(tree) if not attr.startswith("_"))
    raise RuntimeError(
        "no node-group socket API: tree has neither interface.new_socket nor "
        f"outputs.new; available attributes: {available}"
    )


def _is_compositor_group(scene, tree) -> bool:
    """True when *tree* is the Blender 5.0 compositing node group."""
    return tree is not None and tree is getattr(scene, "compositing_node_group", None)


def _setup_passes(scene, output_dir: Path) -> None:
    layer = scene.view_layers[0]
    layer.use_pass_z = True
    layer.use_pass_normal = True
    layer.use_pass_object_index = True
    layer.use_pass_material_index = True

    tree = _compositing_tree(scene)
    tree.nodes.clear()
    render_layers = tree.nodes.new("CompositorNodeRLayers")

    if _is_compositor_group(scene, tree):
        # Blender 5.0: the compositing tree is a node group and the legacy
        # CompositorNodeComposite no longer exists. The scene renders whatever
        # is wired into NodeGroupOutput, so declare an Image output socket and
        # feed the render layer's Image into it.
        socket_name = _declare_group_output_socket(tree, "Image", "NodeSocketColor")
        group_output = tree.nodes.new("NodeGroupOutput")
        try:
            target = group_output.inputs[socket_name]
        except (KeyError, IndexError) as exc:
            available = [socket.name for socket in group_output.inputs]
            raise RuntimeError(
                f"NodeGroupOutput is missing declared socket {socket_name!r}; "
                f"available inputs: {available}"
            ) from exc
        tree.links.new(render_layers.outputs["Image"], target)
    else:
        # Blender 4.x legacy compositor tree: keep the Composite node.
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
    metadata = {
        "schema_version": "0.1.0",
        "scene_revision_id": plan["scene_revision_id"],
        "design_revision_id": plan.get("design_revision_id"),
        "scene_id": plan["scene_id"],
        "renderer_profile": plan.get("renderer_profile", DEFAULT_RENDERER_PROFILE),
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
    if bpy is not None:
        metadata["blender_version"] = bpy.app.version_string
    return metadata


def main() -> int:
    args = _args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    _reset()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    engine = _engine(scene, plan.get("renderer_profile"))
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
