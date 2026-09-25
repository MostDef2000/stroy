from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RenderContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    design_revision_id: str | None = None
    camera_id: str = Field(min_length=1)
    renderer_profile: str = "blender-cycles-v0"


class RenderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    render_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    design_revision_id: str | None = None
    camera_id: str = Field(min_length=1)
    renderer_profile: str | None = None
    passes: dict[str, str] = Field(default_factory=dict)


def finalize_render_manifest(
    *,
    context: RenderContext,
    pass_asset_ids: dict[str, str],
) -> RenderManifest:
    required = {"rgb", "depth", "normals", "object_ids", "material_ids"}
    missing = sorted(required - set(pass_asset_ids))
    if missing:
        raise ValueError("render outputs missing passes: " + ", ".join(missing))
    return RenderManifest(
        render_id=context.render_id,
        scene_revision_id=context.scene_revision_id,
        design_revision_id=context.design_revision_id,
        camera_id=context.camera_id,
        renderer_profile=context.renderer_profile,
        passes={name: pass_asset_ids[name] for name in sorted(required)},
    )
