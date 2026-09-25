from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from stroy.camera.projection import project_world_point, transform_local_point
from stroy.domain.models import Camera, SceneEntity


class ReplacementRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "projected_bbox"
    target_entity_id: str
    camera_id: str
    bbox_px: tuple[int, int, int, int]
    feather_px: int = Field(ge=0)
    source: str = "canonical_entity_geometry"


def _dimensions_mm(entity: SceneEntity) -> tuple[float, float, float]:
    raw = entity.geometry.get("dimensions_mm") or entity.geometry.get("size_mm")
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(
            f"replacement target requires dimensions_mm/size_mm geometry: {entity.id}"
        )
    return tuple(float(value) for value in raw)  # type: ignore[return-value]


def projected_entity_region(
    entity: SceneEntity,
    camera: Camera,
    *,
    margin_ratio: float = 0.08,
    min_feather_px: int = 8,
) -> ReplacementRegion:
    dimensions = _dimensions_mm(entity)
    scale = entity.transform.scale
    half = tuple(
        dimensions[index] * scale[index] / 2.0
        for index in range(3)
    )

    projected: list[tuple[float, float]] = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                local = (
                    half[0] * sx,
                    half[1] * sy,
                    half[2] * sz,
                )
                world = transform_local_point(
                    entity.transform.translation_mm,
                    entity.transform.rotation_deg,
                    local,
                )
                try:
                    u, v, _ = project_world_point(camera, world)
                except ValueError:
                    continue
                projected.append((u, v))

    if not projected:
        raise ValueError(
            f"replacement target is not visible from camera {camera.id}: {entity.id}"
        )

    min_x = min(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_x = max(point[0] for point in projected)
    max_y = max(point[1] for point in projected)

    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    margin = max(float(min_feather_px), max(width, height) * margin_ratio)

    x0 = max(0, math.floor(min_x - margin))
    y0 = max(0, math.floor(min_y - margin))
    x1 = min(camera.width_px, math.ceil(max_x + margin))
    y1 = min(camera.height_px, math.ceil(max_y + margin))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(
            f"replacement target projects outside camera frame: {entity.id}"
        )

    feather = max(
        min_feather_px,
        round(min(x1 - x0, y1 - y0) * 0.08),
    )
    return ReplacementRegion(
        target_entity_id=entity.id,
        camera_id=camera.id,
        bbox_px=(x0, y0, x1, y1),
        feather_px=feather,
    )
