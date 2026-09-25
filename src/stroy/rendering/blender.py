from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stroy.domain.models import Camera, Scene, SceneEntity


PASS_NAMES = ("rgb", "depth", "normals", "object_ids", "material_ids")
_MAX_INDEX = (1 << 24) - 1


def stable_id_map(values: list[str]) -> dict[str, int]:
    used: set[int] = set()
    result: dict[str, int] = {}
    for value in sorted(set(values)):
        candidate = int.from_bytes(
            hashlib.sha256(value.encode("utf-8")).digest()[:3], "big"
        )
        candidate = max(1, candidate)
        while candidate in used:
            candidate += 1
            if candidate > _MAX_INDEX:
                candidate = 1
        used.add(candidate)
        result[value] = candidate
    return result


def _mm3_to_m(values: tuple[float, float, float] | list[float]) -> tuple[float, float, float]:
    return tuple(float(value) / 1000.0 for value in values)  # type: ignore[return-value]


def _dimensions_m(entity: SceneEntity) -> tuple[float, float, float] | None:
    raw = entity.geometry.get("dimensions_mm") or entity.geometry.get("size_mm")
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return None
    return _mm3_to_m(raw)


def _color_for_key(key: str) -> tuple[float, float, float, float]:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return (
        0.2 + (digest[0] / 255.0) * 0.6,
        0.2 + (digest[1] / 255.0) * 0.6,
        0.2 + (digest[2] / 255.0) * 0.6,
        1.0,
    )


class BlenderEntityPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_id: str
    kind: str
    translation_m: tuple[float, float, float]
    rotation_rad: tuple[float, float, float]
    scale: tuple[float, float, float]
    dimensions_m: tuple[float, float, float] | None = None
    material_ref: str
    color_rgba: tuple[float, float, float, float]
    object_index: int = Field(ge=1, le=_MAX_INDEX)
    material_index: int = Field(ge=1, le=_MAX_INDEX)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BlenderCameraPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_id: str
    width_px: int
    height_px: int
    translation_m: tuple[float, float, float]
    rotation_rad: tuple[float, float, float]
    fx: float
    fy: float
    cx: float
    cy: float
    sensor_width_mm: float = 36.0
    lens_mm: float
    pixel_aspect_x: float = 1.0
    pixel_aspect_y: float
    shift_x: float
    shift_y: float
    clip_start_m: float = 0.01
    clip_end_m: float = 1000.0


class BlenderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    scene_revision_id: str
    design_revision_id: str | None = None
    scene_id: str
    camera: BlenderCameraPlan
    entities: list[BlenderEntityPlan]
    passes: list[str] = Field(default_factory=lambda: list(PASS_NAMES))
    renderer_profile: str = "blender-cycles-v0"

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        )


def _camera_plan(camera: Camera) -> BlenderCameraPlan:
    sensor_width_mm = 36.0
    width = float(camera.width_px)
    height = float(camera.height_px)
    fx = camera.intrinsics.fx
    fy = camera.intrinsics.fy
    pixel_aspect_y = fy / fx
    lens_mm = fx * sensor_width_mm / width

    # Blender HORIZONTAL sensor-fit calibration:
    # u0 = W/2 - shift_x * W
    # v0 = H/2 + shift_y * W / pixel_aspect_ratio
    shift_x = (width / 2.0 - camera.intrinsics.cx) / width
    shift_y = (
        (camera.intrinsics.cy - height / 2.0)
        * pixel_aspect_y
        / width
    )
    return BlenderCameraPlan(
        semantic_id=camera.id,
        width_px=camera.width_px,
        height_px=camera.height_px,
        translation_m=_mm3_to_m(camera.transform.translation_mm),
        rotation_rad=tuple(
            math.radians(value) for value in camera.transform.rotation_deg
        ),
        fx=fx,
        fy=fy,
        cx=camera.intrinsics.cx,
        cy=camera.intrinsics.cy,
        sensor_width_mm=sensor_width_mm,
        lens_mm=lens_mm,
        pixel_aspect_y=pixel_aspect_y,
        shift_x=shift_x,
        shift_y=shift_y,
    )


def build_blender_plan(
    scene: Scene,
    *,
    scene_revision_id: str,
    camera_id: str,
    design_revision_id: str | None = None,
    renderer_profile: str = "blender-cycles-v0",
) -> BlenderPlan:
    camera = next((item for item in scene.cameras if item.id == camera_id), None)
    if camera is None:
        raise ValueError(f"unknown camera: {camera_id}")

    renderable = [
        entity
        for entity in scene.entities
        if entity.kind.value != "room"
    ]
    object_indices = stable_id_map([entity.id for entity in renderable])
    material_keys = [
        entity.material_ref or f"material.default.{entity.kind.value}"
        for entity in renderable
    ]
    material_indices = stable_id_map(material_keys)

    entities: list[BlenderEntityPlan] = []
    for entity in sorted(renderable, key=lambda item: item.id):
        material_ref = entity.material_ref or f"material.default.{entity.kind.value}"
        metadata = dict(entity.metadata)
        color = metadata.get("color")
        if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
            try:
                color_rgba = (
                    int(color[1:3], 16) / 255.0,
                    int(color[3:5], 16) / 255.0,
                    int(color[5:7], 16) / 255.0,
                    1.0,
                )
            except ValueError:
                color_rgba = _color_for_key(material_ref)
        else:
            color_rgba = _color_for_key(material_ref)

        entities.append(
            BlenderEntityPlan(
                semantic_id=entity.id,
                kind=entity.kind.value,
                translation_m=_mm3_to_m(entity.transform.translation_mm),
                rotation_rad=tuple(
                    math.radians(value) for value in entity.transform.rotation_deg
                ),
                scale=entity.transform.scale,
                dimensions_m=_dimensions_m(entity),
                material_ref=material_ref,
                color_rgba=color_rgba,
                object_index=object_indices[entity.id],
                material_index=material_indices[material_ref],
                metadata=metadata,
            )
        )

    return BlenderPlan(
        scene_revision_id=scene_revision_id,
        design_revision_id=design_revision_id,
        scene_id=scene.scene_id,
        camera=_camera_plan(camera),
        entities=entities,
        renderer_profile=renderer_profile,
    )


class BlenderAdapter:
    def __init__(
        self,
        blender_bin: str = "blender",
        *,
        script_path: str | Path | None = None,
        timeout_seconds: int = 900,
    ) -> None:
        self.blender_bin = blender_bin
        self.script_path = Path(script_path or "blender/stroy_blender.py")
        self.timeout_seconds = timeout_seconds

    def command(
        self,
        *,
        plan_path: str | Path,
        output_dir: str | Path,
        render: bool = True,
    ) -> list[str]:
        command = [
            self.blender_bin,
            "--background",
            "--factory-startup",
            "--python",
            str(self.script_path),
            "--",
            "--plan",
            str(plan_path),
            "--output-dir",
            str(output_dir),
        ]
        if render:
            command.append("--render")
        return command

    async def run(
        self,
        plan: BlenderPlan,
        *,
        output_dir: str | Path,
        render: bool = True,
    ) -> dict[str, Path]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as handle:
            handle.write(plan.canonical_json())
            plan_path = Path(handle.name)
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command(plan_path=plan_path, output_dir=output, render=render),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise RuntimeError("Blender render timed out") from exc
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace")[-4000:]
                raise RuntimeError(f"Blender failed with exit {process.returncode}: {detail}")
            _ = stdout
        finally:
            plan_path.unlink(missing_ok=True)

        expected = {
            "metadata": output / "scene_metadata.json",
        }
        if render:
            expected.update(
                {
                    "rgb": output / "rgb.png",
                    "depth": output / "depth.exr",
                    "normals": output / "normals.exr",
                    "object_ids": output / "object_ids.exr",
                    "material_ids": output / "material_ids.exr",
                }
            )
        missing = [name for name, path in expected.items() if not path.exists()]
        if missing:
            raise RuntimeError(
                "Blender completed without expected outputs: " + ", ".join(missing)
            )
        return expected
