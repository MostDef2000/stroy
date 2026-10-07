from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import hashlib

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import AssetRow, GenerationManifestRow, JobRow, SceneRevisionRow
from stroy.generation import GenerationManifest, WorkflowManifest
from stroy.services.dispatch import JobDispatcher
from stroy.services.jobs import create_job

DEFAULT_WORKFLOW_PATH = Path("workflows/flux-redesign-v0.manifest.json")
EDIT_WORKFLOW_PATH = Path("workflows/image-edit-kontext-v0.manifest.json")
REDESIGN_REFERENCE_WORKFLOW_PATH = Path(
    "workflows/image-redesign-reference-v1.manifest.json"
)


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
    variant_id: str | None = None,
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
    # R4: optional variant linkage rides the payload into the manifest row.
    if variant_id is not None:
        payload["variant_id"] = variant_id
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
        # R4: variant linkage carried from the job payload (NULL legacy).
        variant_id=job.payload.get("variant_id"),
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



async def resolve_base_asset_for_edit(
    session: AsyncSession,
    project_id: str,
    camera_id: str | None,
    base_revision_id: str,
) -> AssetRow | None:
    # Walk SceneRevisionRow.parent_revision_id from base_revision_id up to root
    ancestors = []
    curr_id = base_revision_id
    while curr_id:
        ancestors.append(curr_id)
        res = await session.execute(
            select(SceneRevisionRow.parent_revision_id).where(SceneRevisionRow.id == curr_id)
        )
        curr_id = res.scalar_one_or_none()

    # Query GenerationManifestRow WHERE project_id matches AND design_revision_id
    # IN ancestors (AND camera_id matches when a camera is supplied - photo-first
    # callers pass None and accept any camera's most recent output).
    # ORDER BY created_at DESC, id DESC
    conditions = [
        GenerationManifestRow.project_id == project_id,
        GenerationManifestRow.design_revision_id.in_(ancestors),
    ]
    if camera_id is not None:
        conditions.append(GenerationManifestRow.camera_id == camera_id)
    result = await session.execute(
        select(GenerationManifestRow)
        .where(*conditions)
        .order_by(
            GenerationManifestRow.created_at.desc(),
            GenerationManifestRow.id.desc(),
        )
    )
    manifests = result.scalars().all()

    for manifest in manifests:
        # Parse manifest_json dict, take output_asset_ids (list), first non-empty row's first asset id
        output_ids = manifest.manifest_json.get("output_asset_ids")
        if not isinstance(output_ids, list) or not output_ids:
            continue

        asset_id = output_ids[0]
        asset = await session.get(AssetRow, asset_id)
        if asset is None:
            continue
        if asset.project_id != project_id:
            continue
        if not asset.media_type.startswith("image/"):
            continue

        return asset

    return None

async def queue_reference_edit(
    session: AsyncSession,
    *,
    project_id: str,
    design_revision_id: str,
    camera_id: str | None,
    request_text: str,
    target_entity_id: str | None,
    reference_asset_id: str,
    affected_region: dict,
    protected_entity_ids: list[str],
    correlation_id: str | None,
    dispatcher: JobDispatcher | None,
    workflow_path: Path = EDIT_WORKFLOW_PATH,
    base_asset_id: str | None = None,
    mask_asset_id: str | None = None,
    reference_subject_bbox: list[int] | None = None,
    ipa_weight: float = 0.85,
    has_reference: bool = True,
) -> JobRow:
    workflow = load_default_workflow(workflow_path)
    generation_id = str(uuid4())
    # No-reference (Remove/Restyle) edits bind a synthetic black placeholder to
    # the reference slot; it must contribute nothing, so the IP-Adapter weight
    # is forced to 0.0 regardless of the requested value. Mirrors
    # queue_reference_redesign. ``reference_asset_id`` stays the placeholder id
    # used by asset_roles / input_asset_ids.
    effective_ipa_weight = ipa_weight if has_reference else 0.0
    # Photo-first (mask_region-only) edits carry no canonical entity/camera.
    # GenerationContext.camera_id and GenerationManifestRow.camera_id are
    # non-nullable, so a stable placeholder keeps the manifest contract valid.
    camera_id = camera_id or "default"
    structured_conditioning = {
        "purpose": "object_replacement",
        "regeneration_scope": "targeted",
        "affected_entity_ids": [target_entity_id] if target_entity_id else [],
        "protected_entity_ids": sorted(set(protected_entity_ids)),
        "replacement_reference_asset_id": (
            reference_asset_id if has_reference else None
        ),
        "affected_region": affected_region,
        "request_text": request_text,
    }
    if base_asset_id and mask_asset_id:
        structured_conditioning["base_asset_id"] = base_asset_id
        structured_conditioning["mask_asset_id"] = mask_asset_id

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
        "affected_entity_ids": [target_entity_id] if target_entity_id else [],
        "regeneration_scope": "targeted",
        "replacement": {
            "target_entity_id": target_entity_id,
            "reference_asset_id": reference_asset_id if has_reference else None,
            "affected_region": affected_region,
        },
    }

    if base_asset_id and mask_asset_id:
        payload["replacement"]["base_asset_id"] = base_asset_id
        payload["replacement"]["mask_asset_id"] = mask_asset_id
        payload["generation"]["input_asset_ids"] = [base_asset_id, reference_asset_id, mask_asset_id]
        payload["input_asset_ids"] = [base_asset_id, reference_asset_id, mask_asset_id]
        # The cropped reference asset feeds ONLY the IPAdapterFlux identity
        # path (control_image, node 25). The ReferenceLatent path (node 12)
        # conditions on the base-scene latent (node 20, VAEEncodeForInpaint),
        # not the reference.
        payload["asset_roles"] = {
            "base_image": base_asset_id,
            "reference_image": reference_asset_id,
            "mask_image": mask_asset_id,
            "control_image": reference_asset_id,
        }

    if reference_subject_bbox:
        payload["reference_subject_bbox"] = reference_subject_bbox
    payload["ipa_weight"] = effective_ipa_weight

    # UUID components are truncated to 12 chars so the key fits the
    # JobRow.idempotency_key String(160) column on Postgres (SQLite does
    # not enforce VARCHAR length, so the overflow is invisible in tests).
    idempotency_key = (
        f"replacement:{design_revision_id[:12]}:{camera_id}:"
        f"{target_entity_id or 'region'}:{reference_asset_id[:12]}"
    )
    if base_asset_id:
        idempotency_key += f":{base_asset_id[:12]}"
    if reference_subject_bbox:
        # Compact, length-bounded suffix: a raw list can overflow the column.
        bbox_hash = hashlib.sha1(str(reference_subject_bbox).encode()).hexdigest()[:12]
        idempotency_key += f":crop-{bbox_hash}"
    if not has_reference:
        # Distinguish no-reference (placeholder-bound) edits from edits that
        # genuinely carry a reference. Mirrors the redesign has_reference flag.
        idempotency_key += ":noreference"
    idempotency_key += f":ipa{effective_ipa_weight}"

    return await create_job(
        session,
        project_id=project_id,
        job_type="image.edit",
        required_capabilities=["image_edit"],
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        dispatcher=dispatcher,
    )


def black_reference_png() -> bytes:
    """1x1 black PNG used as the reference slot when no reference is supplied."""
    buffer = BytesIO()
    Image.new("RGB", (1, 1), (0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


async def create_black_reference_asset(
    session: AsyncSession,
    *,
    object_store: Any,
    project_id: str,
    base_asset_id: str,
    source: str,
    original_name: str,
) -> AssetRow:
    """Store and persist the synthetic black reference asset.

    Shared by the redesign and replacement routes when the caller supplies no
    reference image: the workflow's ``reference_image`` slot must stay bound,
    so a 1x1 black PNG is stored and the IP-Adapter weight is forced to 0.0
    (see :func:`queue_reference_edit` / :func:`queue_reference_redesign`).
    """
    black_bytes = black_reference_png()
    object_key = f"projects/{project_id}/{uuid4()}.png"
    await object_store.put_bytes(object_key, black_bytes, "image/png")
    asset = AssetRow(
        id=str(uuid4()),
        project_id=project_id,
        object_key=object_key,
        original_name=original_name,
        media_type="image/png",
        size_bytes=len(black_bytes),
        sha256=hashlib.sha256(black_bytes).hexdigest(),
        provenance="generated",
        role="reference",
        source_asset_ids=[base_asset_id],
        metadata_json={
            "width_px": 1,
            "height_px": 1,
            "source": source,
        },
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def queue_reference_redesign(
    session: AsyncSession,
    *,
    project_id: str,
    design_revision_id: str,
    request_text: str,
    base_asset_id: str,
    reference_asset_id: str,
    strength: float,
    negative_prompt: str,
    correlation_id: str | None,
    dispatcher: JobDispatcher | None,
    seed: int | None = None,
    has_reference: bool = True,
    workflow_path: Path = REDESIGN_REFERENCE_WORKFLOW_PATH,
) -> JobRow:
    """Queue a reference-based whole-room redesign (img2img over the base photo).

    ``reference_asset_id`` is always the asset bound to the graph's
    reference_image slot. ``has_reference=False`` means prompt-only restyle:
    the caller passes a synthetic 1x1 black asset and the IP-Adapter weight is
    forced to 0.0 so the placeholder contributes nothing.
    """
    workflow = load_default_workflow(workflow_path)
    generation_id = str(uuid4())
    resolved_seed = seed if seed is not None else 0
    ipa_weight = 0.85 if has_reference else 0.0
    # GenerationContext.camera_id / GenerationManifestRow.camera_id are
    # non-nullable; photo-first redesigns have no canonical camera.
    camera_id = "default"
    input_asset_ids = [base_asset_id, reference_asset_id]
    structured_conditioning = {
        "purpose": "room_redesign",
        "regeneration_scope": "full",
        "base_asset_id": base_asset_id,
        "reference_asset_id": reference_asset_id if has_reference else None,
        "strength": strength,
        "request_text": request_text,
    }
    payload = {
        "purpose": "room_redesign",
        "workflow_manifest": workflow.model_dump(mode="json", exclude_none=True),
        "inputs": {
            "prompt": request_text,
            "negative_prompt": negative_prompt,
            "seed": resolved_seed,
            "strength": strength,
        },
        "generation": {
            "generation_id": generation_id,
            "scene_revision_id": design_revision_id,
            "design_revision_id": design_revision_id,
            "camera_id": camera_id,
            "seed": resolved_seed,
            "input_asset_ids": input_asset_ids,
            "structured_conditioning": structured_conditioning,
        },
        "scene_revision_id": design_revision_id,
        "design_revision_id": design_revision_id,
        "camera_id": camera_id,
        "input_asset_ids": input_asset_ids,
        "regeneration_scope": "full",
        "ipa_weight": ipa_weight,
        "redesign": {
            "base_asset_id": base_asset_id,
            "reference_asset_id": reference_asset_id if has_reference else None,
            "strength": strength,
        },
    }
    # base_image always binds; reference_image binds the real reference or the
    # synthetic black placeholder (same asset id the caller stored).
    payload["asset_roles"] = {
        "base_image": base_asset_id,
        "reference_image": reference_asset_id,
    }

    # UUID components truncated to 12 chars to fit JobRow.idempotency_key
    # String(160) on Postgres (SQLite does not enforce VARCHAR length).
    idempotency_key = (
        f"redesign:{design_revision_id[:12]}:{base_asset_id[:12]}:"
        f"{reference_asset_id[:12]}:s{strength}"
    )

    return await create_job(
        session,
        project_id=project_id,
        job_type="image.edit",
        required_capabilities=["image_edit"],
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        dispatcher=dispatcher,
    )
