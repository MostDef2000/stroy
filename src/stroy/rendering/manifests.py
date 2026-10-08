from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RenderContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    design_revision_id: str | None = None
    camera_id: str = Field(min_length=1)
    renderer_profile: str = "blender-cycles-v0"
    # R7: quality stage plus lineage/provenance passthrough from the job
    # payload. Optional so worker results written before these fields existed
    # still validate; provenance dicts default to {} (= nothing to pass
    # through) and are folded to None (absent) when empty on the manifest.
    stage: Literal["draft", "final"] | None = None
    variant_id: str | None = None
    source_asset_ids: list[str] | None = None
    workflow_provenance: dict[str, Any] = Field(default_factory=dict)
    model_provenance: dict[str, Any] = Field(default_factory=dict)


class RenderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    render_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    design_revision_id: str | None = None
    camera_id: str = Field(min_length=1)
    renderer_profile: str | None = None
    # R7 additive fields; None stays out of exclude_none dumps so pre-R7
    # manifests keep validating/serving and the versioned manifest JSON schema
    # (additionalProperties: false) remains compatible for legacy payloads.
    stage: Literal["draft", "final"] | None = None
    variant_id: str | None = None
    source_asset_ids: list[str] | None = None
    workflow_provenance: dict[str, Any] | None = None
    model_provenance: dict[str, Any] | None = None
    passes: dict[str, str] = Field(default_factory=dict)
    # Wall-clock Blender render time, lifted from the worker's scene_metadata
    # (blender/stroy_blender.py `_metadata`). Optional so manifests persisted
    # before this field existed still validate/serve (None).
    render_seconds: float | None = None


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
        stage=context.stage,
        variant_id=context.variant_id,
        source_asset_ids=context.source_asset_ids,
        workflow_provenance=context.workflow_provenance or None,
        model_provenance=context.model_provenance or None,
        passes={name: pass_asset_ids[name] for name in sorted(required)},
    )
