from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class GenerationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    design_revision_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    seed: int | None = None
    input_asset_ids: list[str] = Field(default_factory=list)
    structured_conditioning: dict[str, Any] = Field(default_factory=dict)


class GenerationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    generation_id: str = Field(min_length=1)
    scene_revision_id: str = Field(min_length=1)
    design_revision_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    workflow: WorkflowRef
    model_profile: str = Field(min_length=1)
    seed: int | None = None
    input_asset_ids: list[str] = Field(default_factory=list)
    output_asset_ids: list[str] = Field(default_factory=list)
    structured_conditioning: dict[str, Any] = Field(default_factory=dict)


def finalize_generation_manifest(
    *,
    context: GenerationContext,
    workflow_id: str,
    workflow_version: str,
    model_profile: str,
    output_asset_ids: list[str],
) -> GenerationManifest:
    return GenerationManifest(
        generation_id=context.generation_id,
        scene_revision_id=context.scene_revision_id,
        design_revision_id=context.design_revision_id,
        camera_id=context.camera_id,
        workflow=WorkflowRef(id=workflow_id, version=workflow_version),
        model_profile=model_profile,
        seed=context.seed,
        input_asset_ids=context.input_asset_ids,
        output_asset_ids=output_asset_ids,
        structured_conditioning=context.structured_conditioning,
    )
