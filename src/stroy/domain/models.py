from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EntityKind(StrEnum):
    ROOM = "room"
    WALL = "wall"
    FLOOR = "floor"
    CEILING = "ceiling"
    DOOR = "door"
    WINDOW = "window"
    ARCHITECTURAL = "architectural"
    FURNITURE = "furniture"
    LIGHT = "light"
    UTILITY = "utility"


class ProvenanceSource(StrEnum):
    USER = "user"
    IMPORTED = "imported"
    MEASURED = "measured"
    ESTIMATED = "estimated"
    MODEL_INFERRED = "model_inferred"


class Provenance(BaseModel):
    source: ProvenanceSource
    asset_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class EntityLocks(BaseModel):
    geometry: bool = False
    transform: bool = False
    material: bool = False


class Transform(BaseModel):
    translation_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


class SceneEntity(BaseModel):
    id: str = Field(min_length=1)
    kind: EntityKind
    room_id: str | None = None
    display_name: str | None = None
    transform: Transform = Field(default_factory=Transform)
    geometry: dict[str, Any] = Field(default_factory=dict)
    material_ref: str | None = None
    locks: EntityLocks = Field(default_factory=EntityLocks)
    provenance: Provenance | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CameraTransform(BaseModel):
    translation_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)


class CameraIntrinsics(BaseModel):
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float
    cy: float


class CameraObservation(BaseModel):
    world_mm: tuple[float, float, float]
    image_px: tuple[float, float]
    label: str | None = None


class CameraCalibration(BaseModel):
    quality: float | None = Field(default=None, ge=0, le=1)
    residual: float | None = Field(default=None, ge=0)
    method: Literal["manual", "correspondences", "imported"] = "manual"
    observations: list[CameraObservation] = Field(default_factory=list)


class Camera(BaseModel):
    id: str = Field(min_length=1)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    intrinsics: CameraIntrinsics
    transform: CameraTransform
    source_asset_id: str | None = None
    provenance: Provenance | None = None
    calibration: CameraCalibration | None = None


class Scene(BaseModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    scene_id: str
    project_id: str
    units: Literal["mm"] = "mm"
    coordinate_system: dict[str, str] = Field(
        default_factory=lambda: {"handedness": "right", "up_axis": "Z"}
    )
    entities: list[SceneEntity] = Field(default_factory=list)
    cameras: list[Camera] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "Scene":
        ids = [entity.id for entity in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("scene entity IDs must be unique")
        camera_ids = [camera.id for camera in self.cameras]
        if len(camera_ids) != len(set(camera_ids)):
            raise ValueError("camera IDs must be unique")
        if self.coordinate_system != {"handedness": "right", "up_axis": "Z"}:
            raise ValueError("v0.1 requires a right-handed +Z-up coordinate system")
        return self


class CommandOperation(StrEnum):
    SET_MATERIAL = "set_material"
    SET_COLOR = "set_color"
    ADD_OBJECT = "add_object"
    REMOVE_OBJECT = "remove_object"
    REPLACE_OBJECT_FROM_REFERENCE = "replace_object_from_reference"
    MOVE_OBJECT = "move_object"
    SET_LIGHT_INTENT = "set_light_intent"


class CommandOrigin(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class DesignCommand(BaseModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    command_id: str
    base_revision_id: str
    operation: CommandOperation
    target_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    reference_asset_ids: list[str] = Field(default_factory=list)
    origin: CommandOrigin
    request_text: str | None = None


def canonical_hash(scene: Scene) -> str:
    payload = json.dumps(
        scene.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
