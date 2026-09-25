from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import (
    AssetRow,
    GenerationManifestRow,
    GeometryDiagnosticRow,
    JobRow,
    RenderManifestRow,
    SceneRevisionRow,
    StyleProfileRow,
)
from stroy.domain.models import Scene
from stroy.generation import GenerationManifest, WorkflowManifest
from stroy.rendering import stable_id_map
from stroy.services.dispatch import JobDispatcher
from stroy.services.jobs import create_job


_WORKFLOW_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def load_workflow_manifest(
    workflow_id: str,
    *,
    directory: str | Path = "workflows",
) -> WorkflowManifest:
    if not _WORKFLOW_ID.fullmatch(workflow_id):
        raise ValueError("invalid workflow ID")
    path = Path(directory) / f"{workflow_id}.manifest.json"
    if not path.is_file():
        raise ValueError(f"unknown workflow: {workflow_id}")
    return WorkflowManifest.model_validate_json(path.read_text(encoding="utf-8"))


def style_prompt(profile_json: dict[str, Any], user_text: str) -> str:
    labels = ", ".join(profile_json.get("labels") or [])
    palette = ", ".join(
        str(entry.get("hex"))
        for entry in profile_json.get("palette") or []
        if isinstance(entry, dict) and entry.get("hex")
    )
    materials = ", ".join(
        str(entry.get("name"))
        for entry in profile_json.get("materials") or []
        if isinstance(entry, dict) and entry.get("name")
    )
    negatives = ", ".join(profile_json.get("negative_constraints") or [])
    parts = [
        user_text.strip(),
        f"style: {labels}" if labels else "",
        f"palette: {palette}" if palette else "",
        f"materials: {materials}" if materials else "",
        f"avoid: {negatives}" if negatives else "",
        "preserve architectural geometry, camera, walls, openings and room proportions",
    ]
    return ". ".join(part for part in parts if part)


async def queue_generation_from_render(
    session: AsyncSession,
    render: RenderManifestRow,
    *,
    style_profile_id: str,
    user_text: str,
    seed: int,
    workflow_id: str,
    job_type: str = "image.generate",
    reference_asset_ids: list[str] | None = None,
    target_entity_ids: list[str] | None = None,
    correlation_id: str | None = None,
    dispatcher: JobDispatcher | None = None,
) -> JobRow:
    style = await session.get(StyleProfileRow, style_profile_id)
    if style is None or style.project_id != render.project_id:
        raise ValueError("style profile is not in project")

    workflow = load_workflow_manifest(workflow_id)
    passes = render.manifest_json.get("passes") or {}
    required_passes = ["rgb", "depth", "normals", "object_ids", "material_ids"]
    missing = [name for name in required_passes if not passes.get(name)]
    if missing:
        raise ValueError("render manifest missing control passes: " + ", ".join(missing))

    refs = list(reference_asset_ids or [])
    targets = list(target_entity_ids or [])
    all_inputs = list(dict.fromkeys(
        [passes[name] for name in required_passes]
        + list(style.source_asset_ids or [])
        + refs
    ))

    design_revision_id = render.design_revision_id or render.scene_revision_id
    generation_id = str(uuid4())
    conditioning: dict[str, Any] = {
        "style_profile_id": style_profile_id,
        "control_passes": {name: passes[name] for name in required_passes},
        "target_entity_ids": targets,
        "reference_asset_ids": refs,
        "mode": "localized_edit" if job_type == "image.edit" else "full_redesign",
    }
    if targets:
        conditioning["target_object_indices"] = stable_id_map(targets)

    prompt = style_prompt(style.profile_json, user_text)
    context = {
        "generation_id": generation_id,
        "scene_revision_id": render.scene_revision_id,
        "design_revision_id": design_revision_id,
        "camera_id": render.camera_id,
        "seed": seed,
        "input_asset_ids": all_inputs,
        "structured_conditioning": conditioning,
    }

    return await create_job(
        session,
        project_id=render.project_id,
        job_type=job_type,
        required_capabilities=[
            "image_edit" if job_type == "image.edit" else "image_generation"
        ],
        idempotency_key=f"generation:{generation_id}",
        correlation_id=correlation_id,
        payload={
            "purpose": "generation",
            "generation_id": generation_id,
            "workflow_manifest": workflow.model_dump(mode="json", exclude_none=True),
            "inputs": {"prompt": prompt, "seed": seed},
            "input_asset_ids": all_inputs,
            "generation": context,
            "reference_asset_ids": refs,
            "target_entity_ids": targets,
            "control_render_id": render.id,
        },
        dispatcher=dispatcher,
    )


async def persist_generation_manifest(
    session: AsyncSession,
    job: JobRow,
    result: dict[str, Any],
) -> GenerationManifestRow:
    if job.job_type not in {"image.generate", "image.edit"}:
        raise ValueError("job is not a generation job")
    if not job.project_id:
        raise ValueError("generation job has no project")

    raw = result.get("generation_manifest")
    if not isinstance(raw, dict):
        raise ValueError("generation result requires generation_manifest")
    manifest = GenerationManifest.model_validate(raw)

    existing = (
        await session.execute(
            select(GenerationManifestRow).where(GenerationManifestRow.job_id == job.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = GenerationManifestRow(
        id=manifest.generation_id,
        project_id=job.project_id,
        job_id=job.id,
        scene_revision_id=manifest.scene_revision_id,
        design_revision_id=manifest.design_revision_id,
        camera_id=manifest.camera_id,
        manifest_json=manifest.model_dump(mode="json", exclude_none=True),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def queue_geometry_diagnostic(
    session: AsyncSession,
    generation: GenerationManifestRow,
    *,
    correlation_id: str | None,
    dispatcher: JobDispatcher | None,
) -> JobRow:
    manifest = generation.manifest_json
    outputs = manifest.get("output_asset_ids") or []
    controls = (manifest.get("structured_conditioning") or {}).get("control_passes") or {}
    generated_asset_id = outputs[0] if outputs else None
    reference_asset_id = controls.get("rgb")
    if not generated_asset_id or not reference_asset_id:
        raise ValueError("generation lacks RGB assets for geometry diagnostic")

    return await create_job(
        session,
        project_id=generation.project_id,
        job_type="quality.geometry_check",
        required_capabilities=["geometry_quality"],
        idempotency_key=f"geometry-quality:{generation.id}",
        correlation_id=correlation_id,
        payload={
            "purpose": "geometry_preservation",
            "generation_id": generation.id,
            "scene_revision_id": generation.scene_revision_id,
            "camera_id": generation.camera_id,
            "reference_asset_id": reference_asset_id,
            "generated_asset_id": generated_asset_id,
            "input_asset_ids": [reference_asset_id, generated_asset_id],
        },
        dispatcher=dispatcher,
    )


async def persist_geometry_diagnostic(
    session: AsyncSession,
    job: JobRow,
    result: dict[str, Any],
) -> GeometryDiagnosticRow:
    if job.job_type != "quality.geometry_check":
        raise ValueError("job is not a geometry diagnostic")
    raw = result.get("geometry_diagnostic")
    if not isinstance(raw, dict):
        raise ValueError("quality result requires geometry_diagnostic")
    score = raw.get("score")
    metrics = raw.get("metrics")
    if not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError("geometry diagnostic score must be 0..100")
    if not isinstance(metrics, dict):
        raise ValueError("geometry diagnostic metrics must be an object")

    generation_id = job.payload.get("generation_id")
    if not isinstance(generation_id, str):
        raise ValueError("quality job missing generation_id")

    existing = (
        await session.execute(
            select(GeometryDiagnosticRow).where(
                GeometryDiagnosticRow.generation_id == generation_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = GeometryDiagnosticRow(
        project_id=job.project_id,
        generation_id=generation_id,
        scene_revision_id=job.payload["scene_revision_id"],
        camera_id=job.payload["camera_id"],
        score=score,
        metrics_json=metrics,
        input_asset_ids=list(job.payload.get("input_asset_ids") or []),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row



async def queue_render_then_generation(
    session: AsyncSession,
    *,
    project_id: str,
    scene_revision_id: str,
    camera_id: str,
    style_profile_id: str,
    user_text: str,
    seed: int,
    workflow_id: str = "flux-redesign-v0",
    job_type: str = "image.generate",
    reference_asset_ids: list[str] | None = None,
    target_entity_ids: list[str] | None = None,
    correlation_id: str | None = None,
    dispatcher: JobDispatcher | None = None,
    idempotency_key: str | None = None,
) -> JobRow:
    revision = await session.get(SceneRevisionRow, scene_revision_id)
    if revision is None or revision.project_id != project_id:
        raise ValueError("scene revision is not in project")

    scene = Scene.model_validate(revision.scene_json)
    if not any(camera.id == camera_id for camera in scene.cameras):
        raise ValueError(f"unknown camera: {camera_id}")

    style = await session.get(StyleProfileRow, style_profile_id)
    if style is None or style.project_id != project_id:
        raise ValueError("style profile is not in project")

    refs = list(reference_asset_ids or [])
    for asset_id in refs:
        asset = await session.get(AssetRow, asset_id)
        if asset is None or asset.project_id != project_id:
            raise ValueError(f"reference asset is not in project: {asset_id}")
        if not asset.media_type.startswith("image/"):
            raise ValueError(f"reference asset must be an image: {asset_id}")

    # Validate workflow now, before a long render is queued.
    workflow = load_workflow_manifest(workflow_id)

    render_id = str(uuid4())
    return await create_job(
        session,
        project_id=project_id,
        job_type="render.blender",
        required_capabilities=["blender_render"],
        idempotency_key=idempotency_key or f"generation-render:{render_id}",
        correlation_id=correlation_id,
        payload={
            "purpose": "generation_controls",
            "render_id": render_id,
            "scene_revision_id": revision.id,
            "design_revision_id": revision.id,
            "camera_id": camera_id,
            "scene": revision.scene_json,
            "renderer_profile": "blender-cycles-v0",
            "followup_generation": {
                "style_profile_id": style_profile_id,
                "user_text": user_text,
                "seed": seed,
                "workflow_id": workflow.id,
                "job_type": job_type,
                "reference_asset_ids": refs,
                "target_entity_ids": list(target_entity_ids or []),
            },
        },
        dispatcher=dispatcher,
    )


async def list_generation_manifests(
    session: AsyncSession,
    project_id: str,
) -> list[GenerationManifestRow]:
    result = await session.execute(
        select(GenerationManifestRow)
        .where(GenerationManifestRow.project_id == project_id)
        .order_by(
            GenerationManifestRow.created_at.desc(),
            GenerationManifestRow.id.desc(),
        )
    )
    return list(result.scalars())


async def list_geometry_diagnostics(
    session: AsyncSession,
    project_id: str,
) -> list[GeometryDiagnosticRow]:
    result = await session.execute(
        select(GeometryDiagnosticRow)
        .where(GeometryDiagnosticRow.project_id == project_id)
        .order_by(
            GeometryDiagnosticRow.created_at.desc(),
            GeometryDiagnosticRow.id.desc(),
        )
    )
    return list(result.scalars())
