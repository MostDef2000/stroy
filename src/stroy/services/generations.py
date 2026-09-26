from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import GenerationManifestRow, JobRow
from stroy.generation import GenerationManifest, WorkflowManifest
from stroy.services.dispatch import JobDispatcher
from stroy.services.jobs import create_job


DEFAULT_WORKFLOW_PATH = Path("workflows/flux-redesign-v0.manifest.json")
EDIT_WORKFLOW_PATH = Path("workflows/image-edit-kontext-v0.manifest.json")

def load_default_workflow(path: Path = DEFAULT_WORKFLOW_PATH) -> WorkflowManifest:
    return WorkflowManifest.model_validate_json(path.read_text(encoding="utf-8"))


def ensure_generation_payload(payload: dict[str, Any], job_type: str | None = None) -> dict[str, Any]:
    """Fill in the default generation context for UI-created image jobs.

    The web UI "Test generation" button creates ``image.generate`` jobs with a
    bare payload (a ``scene_revision_id`` at best). Workers validate the
    payload against ``GenerationContext``/``WorkflowManifest`` and the result
    manifest is cross-checked against ``job.payload["generation"]`` on
    completion, so the context must exist before the job is queued. Jobs
    created by :func:`queue_design_generation` already carry a full context
    and are returned unchanged.

    Note: UI "Test generation" provides no prompt field; defaults to "Test generation".
    """
    if isinstance(payload.get("generation"), dict):
        return payload
    scene_revision_id = payload.get("scene_revision_id")
    if not isinstance(scene_revision_id, str) or not scene_revision_id:
        scene_revision_id = "unresolved"
    design_revision_id = payload.get("design_revision_id")
    if not isinstance(design_revision_id, str) or not design_revision_id:
        design_revision_id = scene_revision_id
    camera_id = payload.get("camera_id")
    if not isinstance(camera_id, str) or not camera_id:
        camera_id = "default"
    raw_asset_ids = payload.get("input_asset_ids")
    input_asset_ids = (
        [str(asset_id) for asset_id in raw_asset_ids if isinstance(asset_id, str)]
        if isinstance(raw_asset_ids, list)
        else []
    )
    workflow_manifest = payload.get("workflow_manifest")
    if not isinstance(workflow_manifest, dict):
        manifest_obj = load_default_workflow(EDIT_WORKFLOW_PATH if job_type == "image.edit" else DEFAULT_WORKFLOW_PATH)
        workflow_manifest = manifest_obj.model_dump(
            mode="json", exclude_none=True
        )
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    inputs.setdefault("prompt", str(payload.get("prompt") or "Test generation"))
    inputs.setdefault("seed", 0)
    return {
        **payload,
        "generation": {
            "generation_id": str(uuid4()),
            "scene_revision_id": scene_revision_id,
            "design_revision_id": design_revision_id,
            "camera_id": camera_id,
            "input_asset_ids": input_asset_ids,
        },
        "workflow_manifest": workflow_manifest,
        "inputs": inputs,
    }


async def queue_design_generation(
    session: AsyncSession,
    *,
    project_id: str,
    design_revision_id: str,
    camera_id: str,
    request_text: str,
    affected_entity_ids: list[str],
    protected_entity_ids: list[str],
    reference_asset_ids: list[str],
    correlation_id: str | None,
    dispatcher: JobDispatcher | None,
    workflow_path: Path = DEFAULT_WORKFLOW_PATH,
) -> JobRow:
    workflow = load_default_workflow(workflow_path)
    generation_id = str(uuid4())
    scope = "targeted" if affected_entity_ids and len(affected_entity_ids) <= 3 else "full"
    structured_conditioning = {
        "purpose": "design_edit",
        "regeneration_scope": scope,
        "affected_entity_ids": sorted(set(affected_entity_ids)),
        "protected_entity_ids": sorted(set(protected_entity_ids)),
        "request_text": request_text,
    }
    payload = {
        "purpose": "design_regeneration",
        "workflow_manifest": workflow.model_dump(mode="json", exclude_none=True),
        "inputs": {
            "prompt": request_text,
            "seed": 0,
        },
        "generation": {
            "generation_id": generation_id,
            "scene_revision_id": design_revision_id,
            "design_revision_id": design_revision_id,
            "camera_id": camera_id,
            "seed": 0,
            "input_asset_ids": list(dict.fromkeys(reference_asset_ids)),
            "structured_conditioning": structured_conditioning,
        },
        "scene_revision_id": design_revision_id,
        "design_revision_id": design_revision_id,
        "camera_id": camera_id,
        "input_asset_ids": list(dict.fromkeys(reference_asset_ids)),
        "affected_entity_ids": sorted(set(affected_entity_ids)),
        "regeneration_scope": scope,
    }
    return await create_job(
        session,
        project_id=project_id,
        job_type="image.generate",
        required_capabilities=["image_generation"],
        payload=payload,
        idempotency_key=f"design-generation:{design_revision_id}:{camera_id}",
        correlation_id=correlation_id,
        dispatcher=dispatcher,
    )


async def persist_generation_manifest(
    session: AsyncSession,
    job: JobRow,
    result: dict,
) -> GenerationManifestRow:
    if job.job_type not in {"image.generate", "image.edit"}:
        raise ValueError("job is not an image generation job")
    if not job.project_id:
        raise ValueError("generation job has no project")

    raw = result.get("generation_manifest")
    if not isinstance(raw, dict):
        raise ValueError("generation result requires generation_manifest")
    manifest = GenerationManifest.model_validate(raw)

    expected = job.payload.get("generation") or {}
    if manifest.generation_id != expected.get("generation_id"):
        raise ValueError("generation manifest ID does not match job")
    if manifest.scene_revision_id != expected.get("scene_revision_id"):
        raise ValueError("generation manifest scene revision does not match job")
    if manifest.design_revision_id != expected.get("design_revision_id"):
        raise ValueError("generation manifest design revision does not match job")
    if manifest.camera_id != expected.get("camera_id"):
        raise ValueError("generation manifest camera does not match job")

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



async def queue_reference_edit(
    session: AsyncSession,
    *,
    project_id: str,
    design_revision_id: str,
    camera_id: str,
    request_text: str,
    target_entity_id: str,
    reference_asset_id: str,
    affected_region: dict,
    protected_entity_ids: list[str],
    correlation_id: str | None,
    dispatcher: JobDispatcher | None,
    workflow_path: Path = EDIT_WORKFLOW_PATH,
) -> JobRow:
    workflow = load_default_workflow(workflow_path)
    generation_id = str(uuid4())
    structured_conditioning = {
        "purpose": "object_replacement",
        "regeneration_scope": "targeted",
        "affected_entity_ids": [target_entity_id],
        "protected_entity_ids": sorted(set(protected_entity_ids)),
        "replacement_reference_asset_id": reference_asset_id,
        "affected_region": affected_region,
        "request_text": request_text,
    }
    payload = {
        "purpose": "object_replacement",
        "workflow_manifest": workflow.model_dump(mode="json", exclude_none=True),
        "inputs": {
            "prompt": request_text,
            "seed": 0,
        },
        "generation": {
            "generation_id": generation_id,
            "scene_revision_id": design_revision_id,
            "design_revision_id": design_revision_id,
            "camera_id": camera_id,
            "seed": 0,
            "input_asset_ids": [reference_asset_id],
            "structured_conditioning": structured_conditioning,
        },
        "scene_revision_id": design_revision_id,
        "design_revision_id": design_revision_id,
        "camera_id": camera_id,
        "input_asset_ids": [reference_asset_id],
        "affected_entity_ids": [target_entity_id],
        "regeneration_scope": "targeted",
        "replacement": {
            "target_entity_id": target_entity_id,
            "reference_asset_id": reference_asset_id,
            "affected_region": affected_region,
        },
    }
    return await create_job(
        session,
        project_id=project_id,
        job_type="image.edit",
        required_capabilities=["image_edit"],
        payload=payload,
        idempotency_key=(
            f"replacement:{design_revision_id}:{camera_id}:"
            f"{target_entity_id}:{reference_asset_id}"
        ),
        correlation_id=correlation_id,
        dispatcher=dispatcher,
    )
