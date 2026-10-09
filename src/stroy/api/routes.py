from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.agent import AgentToolError, TOOL_DEFINITIONS
from stroy.api.dependencies import (
    DbSession,
    OwnerSession,
    require_csrf,
    require_owner,
    require_worker,
)
from stroy.db.models import (
    AssetRow,
    AttachmentRow,
    AuthSessionRow,
    BudgetItemRow,
    GenerationManifestRow,
    GeometryDiagnosticRow,
    JobRow,
    ProjectRow,
    RenderManifestRow,
    SceneRevisionRow,
    StyleProfileRow,
    WorkerRow,
)
from stroy.adapters.products.generic import GenericHtmlExtractor
from stroy.net.guard import GuardError
from stroy.services.products import (
    candidate_view,
    create_candidate,
    create_from_url,
    delete_candidate,
    extraction_status,
    get_candidate,
    list_candidates,
    missing_candidate_fields,
    patch_candidate,
)
from stroy.services.budget import (
    BudgetError,
    add_budget_item,
    budget_item_view,
    budget_report,
    delete_budget_item,
    list_budget_items,
    patch_budget_item,
)
from stroy.services.variants import (
    VariantError,
    apply_variant_command,
    compare_variants,
    create_variant_from_current,
    delete_variant,
    fork_variant,
    get_variant,
    list_variants,
    patch_variant,
    restore_variant_revision,
    variant_lineage_ids,
    variant_view,
)
from stroy.domain.commands import CommandConflict, CommandRejected
from stroy.domain.models import Camera, DesignCommand, EntityIntent, EntityKind, Scene, SceneEntity
from stroy.domain.plan import PlanDraft
from stroy.security import random_token, sha256_text, verify_password
from stroy.services.agent import apply_design_agent_result
from stroy.services.asset_metadata import extract_asset_metadata
from stroy.services.cameras import remove_camera, upsert_camera
from stroy.editing import resolve_replacement_region
from stroy.editing.mask import render_replacement_mask
from stroy.services.generations import (
    create_black_reference_asset,
    ensure_generation_payload,
    list_generation_manifests,
    persist_generation_manifest,
    queue_design_generation,
    queue_reference_edit,
    queue_reference_redesign,
    resolve_base_asset_for_edit,
)
from stroy.services.jobs import (
    ACTIVE_JOB_STATUSES,
    cancel_job,
    claim_job,
    complete_job,
    create_job,
    fail_job,
    release_job,
    renew_lease,
    update_progress,
)
from stroy.services.moods import MoodPreset, compose_redesign_request_text, get_mood
from stroy.services.quality import list_geometry_diagnostics, persist_geometry_diagnostic
from stroy.services.renders import list_render_manifests, persist_render_manifest
from stroy.services.styles import create_style_profile_from_job, list_style_profiles
from stroy.services.plans import (
    DraftAlreadyCommittedError,
    ScaleUnknownError,
    commit_draft,
    create_plan_draft_from_job,
    get_latest_draft,
    save_draft,
)
from stroy.services.projects import ProjectHasActiveJobsError, delete_project
from stroy.services.setup import build_setup
from stroy.services.scenes import (
    apply_scene_command,
    create_noop_revision,
    create_project,
    initialize_scene,
    latest_revision,
    list_revisions,
    revert_scene,
)
from stroy.services.validation import (
    ValidationConfigError,
    ValidationRevisionNotFoundError,
    latest_validation_report,
    run_validation,
    validation_view,
)


router = APIRouter()


logger = logging.getLogger("stroy.api")


def _domain_conflict(exc: ValueError) -> HTTPException:
    code = "revision_conflict" if isinstance(exc, CommandConflict) else "command_rejected"
    return HTTPException(
        status_code=409,
        detail={"code": code, "detail": str(exc)},
    )


# Room shell kinds are always off-limits for the image-edit pipeline.
_PROTECTED_SHELL_KINDS = frozenset({"wall", "floor", "ceiling", "door", "window"})


def _protected_entity_ids(scene: Scene, *, include_shell: bool) -> list[str]:
    """Entities the image-edit pipeline must not touch.

    R1 protected locked geometry/transform and room shells; R2 adds entities
    with an existence lock or a keep intent (removal/replacement protection).
    One shared helper serves every protected-ids call site.
    """
    return [
        entity.id
        for entity in scene.entities
        if (
            entity.locks.geometry
            or entity.locks.transform
            or entity.locks.existence
            or entity.intent is EntityIntent.KEEP
            or (include_shell and entity.kind.value in _PROTECTED_SHELL_KINDS)
        )
    ]


def _media_type_allowed(media_type: str, configured: str) -> bool:
    allowed = [item.strip().lower() for item in configured.split(",") if item.strip()]
    value = media_type.lower()
    return any(
        rule == value or (rule.endswith("/*") and value.startswith(rule[:-1]))
        for rule in allowed
    )


async def _validated_upload(file: UploadFile, request: Request) -> tuple[bytes, str]:
    settings = request.app.state.settings
    media_type = (file.content_type or "application/octet-stream").lower()
    if not _media_type_allowed(media_type, settings.upload_allowed_media_types):
        raise HTTPException(status_code=415, detail=f"unsupported media type: {media_type}")
    data = await file.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="upload exceeds configured size limit")
    return data, media_type


class LoginRequest(BaseModel):
    username: str
    password: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class BriefNotesPatch(BaseModel):
    """R8 designer-brief notes (#198). Both fields optional: a field absent
    from the body leaves the stored value untouched (partial update), an
    explicit null (or empty/whitespace string) clears it."""

    needs_wishes: str | None = Field(default=None, max_length=8000)
    questions_to_discuss: str | None = Field(default=None, max_length=8000)


class DesignSelectionContext(BaseModel):
    """R10 (#186): optional "what is selected in the UI" hint attached to a
    design instruction, so deictic wording ("этот/его/здесь") resolves to the
    selected entity."""

    entity_id: str = Field(min_length=1, max_length=300)
    kind: str = Field(min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=300)


class DesignInstruction(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str | None = Field(default=None, max_length=160)
    selection_context: DesignSelectionContext | None = None


def selection_context_message(context: DesignSelectionContext) -> dict[str, str]:
    """Short developer-style context prepended to the LLM messages when the
    UI reports a selection. Absent context -> caller sends messages unchanged."""
    title = context.title or context.kind
    return {
        "role": "system",
        "content": (
            f"Выбран объект: «{title}» ({context.kind}, id {context.entity_id}). "
            "Если инструкция говорит «этот/его/здесь», относись к выбранному объекту. "
            "Действия цвета/материала не меняют геометрию."
        ),
    }


class StyleAnalyzeRequest(BaseModel):
    source_text: str | None = Field(default=None, max_length=4000)
    reference_asset_ids: list[str] = Field(min_length=3, max_length=5)
    overrides: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=160)


class PlanAnalyzeHints(BaseModel):
    known_wall_length_mm: float | None = Field(default=None, gt=0)
    wall_asset_index: int | None = Field(default=None, ge=0)
    length_mm: float | None = Field(default=None, gt=0)


class PlanAnalyzeRequest(BaseModel):
    asset_ids: list[str] = Field(min_length=1)
    hints: PlanAnalyzeHints = Field(default_factory=PlanAnalyzeHints)
    idempotency_key: str | None = Field(default=None, max_length=160)


class PlanDraftSave(BaseModel):
    draft: PlanDraft


class CameraUpsertRequest(BaseModel):
    base_revision_id: str = Field(min_length=1)
    camera: Camera
    solve: bool = False


class CameraDeleteRequest(BaseModel):
    base_revision_id: str = Field(min_length=1)


class GeometryDiagnosticRequest(BaseModel):
    scene_revision_id: str
    camera_id: str = Field(min_length=1)
    reference_asset_id: str
    generated_asset_id: str
    protected_mask_asset_id: str | None = None
    tolerance_px: int = Field(default=2, ge=0, le=16)
    edge_threshold: int = Field(default=24, ge=1, le=255)
    advisory_threshold: float = Field(default=0.72, ge=0, le=1)
    idempotency_key: str | None = Field(default=None, max_length=160)


class ValidationRequest(BaseModel):
    # Validation is read-only: it never mutates the scene. Omitting
    # scene_revision_id validates the latest revision with the default config.
    scene_revision_id: str | None = None
    min_walkway_mm: float | None = None


class ReplacementRequest(BaseModel):
    base_revision_id: str = Field(min_length=1)
    # Photo-first replacement: a client mask_region fully describes the edit,
    # so the canonical scene entity/camera are optional. When mask_region is
    # absent BOTH remain required (the server projects the entity region).
    target_entity_id: str | None = Field(default=None, min_length=1)
    reference_asset_id: str | None = Field(default=None, min_length=1)
    camera_id: str | None = Field(default=None, min_length=1)
    base_asset_id: str | None = Field(default=None, min_length=1)
    reference_subject_bbox: list[int] | None = Field(default=None)
    prompt: str = Field(
        default="replace selected furniture with the reference object",
        min_length=1,
        max_length=4000,
    )
    ipa_weight: float = Field(
        default=0.85,
        ge=0.0,
        le=2.0,
        description="IP-Adapter identity strength for FLUX replacement",
    )
    mask_region: list[int] | None = Field(
        default=None,
        description="Optional [x1, y1, x2, y2] mask box in base-image pixels; overrides the server-projected region.",
    )
    shape: Literal["rectangle", "silhouette"] = Field(
        default="rectangle",
        description=(
            "Mask shape. 'rectangle' (default) keeps the feathered bbox mask; "
            "'silhouette' derives a subject silhouette from the base image, "
            "falling back to the rectangle when segmentation is unavailable."
        ),
    )

    @model_validator(mode="after")
    def _validate_reference_subject_bbox(self) -> "ReplacementRequest":
        bbox = self.reference_subject_bbox
        if bbox is not None:
            if len(bbox) != 4 or any(v < 0 for v in bbox) or bbox[2] <= 0 or bbox[3] <= 0:
                raise ValueError(
                    "reference_subject_bbox must be [x, y, w, h] with w,h > 0 and x,y >= 0"
                )
        region = self.mask_region
        if region is not None:
            if (
                len(region) != 4
                or any(v < 0 for v in region)
                or region[0] >= region[2]
                or region[1] >= region[3]
            ):
                raise ValueError(
                    "mask_region must be [x1, y1, x2, y2] with x1<x2, y1<y2, all >= 0"
                )
        else:
            # No client mask_region -> the server must project the canonical
            # entity region, which requires both the target entity and camera.
            missing = [
                name
                for name, value in (
                    ("target_entity_id", self.target_entity_id),
                    ("camera_id", self.camera_id),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "target_entity_id and camera_id are required when "
                    "mask_region is absent (missing: " + ", ".join(missing) + ")"
                )
        return self


class RedesignRequest(BaseModel):
    base_revision_id: str = Field(min_length=1)
    base_asset_id: str | None = Field(default=None, min_length=1)
    reference_asset_id: str | None = Field(default=None, min_length=1)
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(
        default="blurry, distorted, low quality, watermark, text",
        max_length=4000,
    )
    strength: float = Field(default=0.6, ge=0.2, le=0.95)
    seed: int | None = None
    # R9: optional editorial mood preset; prepended to the prompt as an
    # editorial direction. Absent -> prompt passes through unchanged.
    mood_id: str | None = Field(default=None, min_length=1, max_length=64)


class GenerationRequest(BaseModel):
    design_revision_id: str | None = None
    camera_id: str = Field(min_length=1)
    prompt: str = Field(default="redesign room", min_length=1, max_length=4000)
    reference_asset_ids: list[str] = Field(default_factory=list)
    # R4: link the generation to a scene variant (revision must lie in the
    # variant's lineage when supplied).
    variant_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, max_length=160)


class RenderRequest(BaseModel):
    scene_revision_id: str | None = None
    design_revision_id: str | None = None
    camera_id: str = Field(min_length=1)
    renderer_profile: str = "blender-cycles-v0"
    # R7: quality stage. "draft" maps to the fast preview profile only when
    # the client does not pin renderer_profile explicitly; "final" keeps the
    # production default.
    stage: Literal["draft", "final"] = "final"
    # R4: link the render to a scene variant (revision must lie in the
    # variant's lineage when supplied; defaults to the variant head).
    variant_id: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, max_length=160)


class SceneRevert(BaseModel):
    expected_base_revision_id: str
    target_revision_id: str


class AttachmentCreate(BaseModel):
    target_type: Literal["project", "room", "entity"]
    target_id: str | None = Field(default=None, max_length=255)
    kind: Literal["photo", "note", "file", "task"]
    asset_id: str | None = Field(default=None, min_length=1)
    body: str | None = Field(default=None, max_length=8000)
    due_date: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_target_and_asset(self) -> "AttachmentCreate":
        if self.target_type == "project":
            if self.target_id is not None:
                raise ValueError(
                    "project-level attachments cannot carry target_id"
                )
        elif not self.target_id:
            raise ValueError(
                f"target_id is required for target_type={self.target_type}"
            )
        if self.kind in {"photo", "file"} and self.asset_id is None:
            raise ValueError(f"kind={self.kind} requires asset_id")
        return self


class AttachmentPatch(BaseModel):
    # R1: no retargeting (target_type/target_id/asset_id/kind are immutable).
    body: str | None = Field(default=None, max_length=8000)
    done: bool | None = None
    due_date: date | None = None
    metadata: dict[str, Any] | None = None


class ProductImportUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ProductCandidateCreate(BaseModel):
    source_asset_id: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, max_length=300)
    brand: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    # Numeric(12,2) column scale caps the magnitude at 9_999_999_999.99.
    price: float | None = Field(default=None, ge=0, le=9_999_999_999.99)
    currency: str | None = Field(default=None, max_length=12)
    width_mm: float | None = Field(default=None, gt=0)
    depth_mm: float | None = Field(default=None, gt=0)
    height_mm: float | None = Field(default=None, gt=0)
    material_descriptors: list[str] | None = None
    color_descriptors: list[str] | None = None
    three_d_ref: str | None = Field(default=None, max_length=2000)


class ProductCandidatePatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    brand: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    price: float | None = Field(default=None, ge=0, le=9_999_999_999.99)
    currency: str | None = Field(default=None, max_length=12)
    width_mm: float | None = Field(default=None, gt=0)
    depth_mm: float | None = Field(default=None, gt=0)
    height_mm: float | None = Field(default=None, gt=0)
    material_descriptors: list[str] | None = None
    color_descriptors: list[str] | None = None


class VariantCreateFromCurrent(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class VariantForkRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    from_revision_id: str | None = Field(default=None, min_length=1)


class VariantPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, max_length=20)


class VariantRestoreRevision(BaseModel):
    target_revision_id: str = Field(min_length=1)
    expected_head_revision_id: str = Field(min_length=1)


class VariantCommandRequest(BaseModel):
    # Command envelope: the design command plus the optimistic-lock head the
    # caller observed. The command applies to the VARIANT head scene.
    command: DesignCommand
    expected_head_revision_id: str = Field(min_length=1)


class BudgetItemCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=20)
    product_candidate_id: str | None = Field(default=None, min_length=1)
    label: str = Field(min_length=1, max_length=200)
    amount: float | None = Field(default=None, ge=0, le=9_999_999_999.99)
    currency: str | None = Field(default=None, max_length=12)
    quantity: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BudgetItemPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    amount: float | None = Field(default=None, ge=0, le=9_999_999_999.99)
    currency: str | None = Field(default=None, max_length=12)
    quantity: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] | None = None


class JobCreate(BaseModel):
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    idempotency_key: str | None = Field(default=None, max_length=160)


class WorkerRegistration(BaseModel):
    schema_version: str = "0.1.0"
    worker_id: str
    display_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    runtimes: dict[str, Any] = Field(default_factory=dict)
    hardware: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeat(BaseModel):
    worker_id: str


class WorkerClaim(BaseModel):
    worker_id: str


class LeaseRequest(BaseModel):
    worker_id: str
    lease_id: str


class JobProgress(LeaseRequest):
    progress: dict[str, Any] = Field(default_factory=dict)
    runtime_provenance: dict[str, Any] = Field(default_factory=dict)


class JobComplete(LeaseRequest):
    result: dict[str, Any] = Field(default_factory=dict)
    runtime_provenance: dict[str, Any] = Field(default_factory=dict)


class JobError(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    detail: str = Field(min_length=1, max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)


class JobFail(LeaseRequest):
    error: JobError
    runtime_provenance: dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, session: DbSession) -> dict[str, Any]:
    await session.execute(text("SELECT 1"))
    storage_ready = await request.app.state.object_store.ready()
    dispatch_ready = await request.app.state.job_dispatcher.ready()
    if not storage_ready or not dispatch_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "dependency_not_ready",
                "storage": storage_ready,
                "dispatcher": dispatch_ready,
            },
        )
    return {
        "status": "ready",
        "dependencies": {
            "database": True,
            "storage": storage_ready,
            "dispatcher": dispatch_ready,
        },
    }


@router.post("/api/v1/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response, session: DbSession):
    settings = request.app.state.settings
    client_key = request.client.host if request.client else "unknown"
    retry_after = request.app.state.login_throttle.retry_after(client_key)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    valid = payload.username == settings.auth_username and verify_password(
        settings.auth_password_hash, payload.password
    )
    if not valid:
        request.app.state.login_throttle.failure(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    request.app.state.login_throttle.success(client_key)
    token = random_token()
    csrf = random_token(24)
    row = AuthSessionRow(
        token_hash=sha256_text(token),
        csrf_token=csrf,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.session_max_age_seconds),
    )
    session.add(row)
    await session.commit()
    response.set_cookie(
        "stroy_session",
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    return {"authenticated": True, "username": settings.auth_username, "csrf_token": csrf}


@router.get("/api/v1/auth/me")
async def me(request: Request, owner: OwnerSession):
    return {
        "authenticated": True,
        "username": request.app.state.settings.auth_username,
        "csrf_token": owner.csrf_token,
    }


@router.post("/api/v1/auth/logout", dependencies=[Depends(require_csrf)])
async def logout(response: Response, session: DbSession, owner: OwnerSession):
    owner.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    response.delete_cookie("stroy_session", path="/")
    return {"authenticated": False}


@router.get("/api/v1/projects", dependencies=[Depends(require_owner)])
async def projects(session: DbSession):
    result = await session.execute(select(ProjectRow).order_by(ProjectRow.created_at.desc()))
    return [{"id": row.id, "name": row.name, "created_at": row.created_at} for row in result.scalars()]


@router.post("/api/v1/projects", status_code=201, dependencies=[Depends(require_csrf)])
async def project_create(payload: ProjectCreate, session: DbSession, owner: OwnerSession):
    row = await create_project(session, payload.name)
    return {"id": row.id, "name": row.name, "created_at": row.created_at}


@router.delete(
    "/api/v1/projects/{project_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def project_delete(
    project_id: str,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        deleted = await delete_project(session, project_id)
    except ProjectHasActiveJobsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "project_busy", "detail": str(exc)},
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="project not found")
    # The database is authoritative: rows are already committed.  Stored files
    # are best-effort cleanup and must never fail the request.
    try:
        await request.app.state.object_store.delete_prefix(f"projects/{project_id}/")
    except Exception:
        logger.exception("project file cleanup failed", extra={"project_id": project_id})
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# designer brief (R8, #198)
# ---------------------------------------------------------------------------

_BRIEF_SCHEMA_VERSION = "1.0"
_BRIEF_WARNINGS = (
    "Бриф не является строительной или рабочей документацией; "
    "детали требуют проверки специалистом.",
    "Сгенерированные изображения — концепты, а не фотографии.",
)
_BRIEF_BUDGET_DISCLAIMER = "Приблизительно; не является коммерческим предложением"
_BRIEF_RENDER_LABEL = "Концепт, не фотография"
_BRIEF_SKIPPED_RENDER_WARNING = (
    "Некоторые сохранённые рендеры пропущены: нет изображения для предпросмотра."
)
# PlanEditor sources: role "plan" (R6 uploads) and legacy "apartment" images.
_BRIEF_PLAN_ASSET_ROLES = ("plan", "apartment")
_BRIEF_SCALE_LABELS = {
    "confirmed": "Масштаб задан вручную",
    "approximate": "Масштаб распознан с плана (приблизительный)",
    "unknown": "Масштаб не задан",
}


def _brief_notes_view(row: ProjectRow) -> dict[str, Any]:
    notes = row.brief_notes_json if isinstance(row.brief_notes_json, dict) else {}
    return {
        "needs_wishes": notes.get("needs_wishes"),
        "questions_to_discuss": notes.get("questions_to_discuss"),
    }


@router.get(
    "/api/v1/projects/{project_id}/brief-notes",
    dependencies=[Depends(require_owner)],
)
async def brief_notes_get(project_id: str, session: DbSession):
    row = await session.get(ProjectRow, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _brief_notes_view(row)


@router.patch(
    "/api/v1/projects/{project_id}/brief-notes",
    dependencies=[Depends(require_csrf)],
)
async def brief_notes_patch(
    project_id: str,
    payload: BriefNotesPatch,
    session: DbSession,
    owner: OwnerSession,
):
    def _normalize(value: str | None) -> str | None:
        # Empty/whitespace strings normalize to null on write.
        if value is None or not value.strip():
            return None
        return value

    row = await session.get(ProjectRow, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    notes = dict(row.brief_notes_json) if isinstance(row.brief_notes_json, dict) else {}
    updates = payload.model_dump(exclude_unset=True)
    for field in ("needs_wishes", "questions_to_discuss"):
        if field in updates:
            notes[field] = _normalize(updates[field])
    if not notes.get("needs_wishes") and not notes.get("questions_to_discuss"):
        # Both fields null → store NULL; "no notes" has exactly one shape.
        row.brief_notes_json = None
    else:
        row.brief_notes_json = {
            "needs_wishes": notes.get("needs_wishes"),
            "questions_to_discuss": notes.get("questions_to_discuss"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    await session.commit()
    await session.refresh(row)
    return _brief_notes_view(row)


def _brief_scale_view(draft_json: Any) -> dict[str, Any]:
    """Scale block from the latest plan draft's ``draft_json.scale``."""
    scale = draft_json.get("scale") if isinstance(draft_json, dict) else None
    source = scale.get("source") if isinstance(scale, dict) else None
    if source == "manual":
        status = "confirmed"
    elif source == "plan_label":
        status = "approximate"
    else:
        status, source = "unknown", "unknown"
    view: dict[str, Any] = {
        "status": status,
        "source": source,
        "label": _BRIEF_SCALE_LABELS[status],
    }
    if status != "unknown" and isinstance(scale, dict):
        mm_per_px = scale.get("mm_per_px")
        if (
            isinstance(mm_per_px, (int, float))
            and not isinstance(mm_per_px, bool)
            and mm_per_px > 0
        ):
            view["mm_per_px"] = float(mm_per_px)
    return view


def _brief_scene_sections(
    scene_json: Any,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """(rooms, furniture_intents) from a head-revision scene.

    Legacy revisions without a parseable scene degrade to empty sections.
    Rooms are room-kind entities; furniture_intents group entities by their
    keep|remove|replace intent plus a ``locked`` group (existence lock).
    """
    if not isinstance(scene_json, dict) or not scene_json:
        return [], {}
    try:
        scene = Scene.model_validate(scene_json)
    except ValidationError:
        return [], {}

    rooms = [
        {"id": entity.id, "name": entity.display_name}
        for entity in scene.entities
        if entity.kind is EntityKind.ROOM
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for entity in scene.entities:
        entry: dict[str, Any] = {"id": entity.id, "kind": entity.kind.value}
        if entity.display_name:
            entry["name"] = entity.display_name
        if entity.room_id:
            entry["room_id"] = entity.room_id
        locks = entity.locks.model_dump(mode="json", exclude_none=True)
        if any(locks.values()):
            entry["locks"] = locks
        if entity.provenance is not None:
            entry["provenance"] = entity.provenance.model_dump(
                mode="json", exclude_none=True
            )
        if entity.intent is not None:
            groups.setdefault(entity.intent.value, []).append(entry)
        if entity.locks.existence:
            groups.setdefault("locked", []).append(entry)
    return rooms, {key: group for key, group in groups.items() if group}


def _append_unique(warnings: list[str], warning: str) -> None:
    """Warnings append-only with dedupe."""
    if warning not in warnings:
        warnings.append(warning)


def _brief_renders_view(
    rows: list[RenderManifestRow],
) -> tuple[list[dict[str, Any]], int]:
    """Render views; returns (views, skipped_rgb_less_count).

    The 12-render cap is applied AFTER the rgb filter (the caller fetches a
    wider window), so a few rgb-less rows can never shrink the visible list
    below 12 while older rgb-ful renders exist. ``skipped`` counts rgb-less
    rows seen before the cap filled — they are saved renders the brief cannot
    show, which the aggregate surfaces as a warning instead of dropping
    silently. Rows beyond the cap are excluded by the cap, not counted.
    """
    views: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        if len(views) >= 12:
            break  # cap reached; anything after is cap-excluded, not skipped
        manifest = row.manifest_json if isinstance(row.manifest_json, dict) else {}
        passes = manifest.get("passes")
        rgb_asset_id = passes.get("rgb") if isinstance(passes, dict) else None
        if not rgb_asset_id:
            skipped += 1
            continue  # without an RGB pass there is nothing to show a designer
        view: dict[str, Any] = {
            "id": row.id,
            "asset_id": rgb_asset_id,
            "camera_id": row.camera_id,
            "stage": manifest.get("stage"),
            "label": _BRIEF_RENDER_LABEL,
            "renderer_profile": manifest.get("renderer_profile"),
            "created_at": row.created_at,
        }
        provenance: dict[str, Any] = {}
        for section, key in (("workflow", "workflow_provenance"), ("model", "model_provenance")):
            value = manifest.get(key)
            if isinstance(value, dict) and value:
                provenance[section] = value
        if provenance:
            view["workflow_model_provenance"] = provenance
        views.append(view)
    return views, skipped


def _brief_budget_view(items: list[BudgetItemRow]) -> dict[str, Any]:
    views: list[dict[str, Any]] = []
    for item in items:
        view: dict[str, Any] = {"id": item.id, "kind": item.kind, "label": item.label}
        if item.amount is not None:
            view["amount"] = float(item.amount)
        if item.currency:
            view["currency"] = item.currency
        if item.quantity is not None:
            view["quantity"] = float(item.quantity)
        views.append(view)
    return {"disclaimer": _BRIEF_BUDGET_DISCLAIMER, "items": views}


@router.get(
    "/api/v1/projects/{project_id}/design-brief",
    dependencies=[Depends(require_owner)],
)
async def design_brief_get(
    project_id: str,
    session: DbSession,
    variant_id: str | None = None,
):
    """Read-only designer-brief aggregate (R8, #198).

    Composes a BriefDocument from the project, the target variant's head
    scene revision, brief notes, the latest plan draft, renders, photo
    attachments, the latest style profile and the variant's budget items.
    Strictly read-only: no rows are created or committed anywhere here.
    """
    project = await session.get(ProjectRow, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    if variant_id is not None:
        variant = await get_variant(session, project_id, variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="variant not found")
    else:
        approved = await list_variants(session, project_id, status="approved")
        if not approved:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "brief_variant_required",
                    "detail": (
                        "no approved variant: approve a variant or pass "
                        "variant_id explicitly"
                    ),
                },
            )
        variant = approved[0]

    # Warnings: always-present array; entries append-only with dedupe.
    warnings: list[str] = []
    for warning in _BRIEF_WARNINGS:
        _append_unique(warnings, warning)

    head_revision = await session.get(SceneRevisionRow, variant.head_scene_revision_id)
    brief: dict[str, Any] = {"schema_version": _BRIEF_SCHEMA_VERSION}
    brief["project"] = {
        "id": project.id,
        "name": project.name,
        "created_at": project.created_at,
    }
    brief["variant"] = {
        "id": variant.id,
        "title": variant.title,
        "status": variant.status,
        "base_scene_revision_id": variant.base_scene_revision_id,
        "head_scene_revision_id": variant.head_scene_revision_id,
        "head_scene_revision_hash": (
            head_revision.content_hash if head_revision is not None else None
        ),
        "head_scene_revision_created_at": (
            head_revision.created_at if head_revision is not None else None
        ),
    }

    # notes: omit the section when both fields are absent.
    notes = (
        project.brief_notes_json if isinstance(project.brief_notes_json, dict) else {}
    )
    notes_view = {
        key: notes[key]
        for key in ("needs_wishes", "questions_to_discuss")
        if notes.get(key)
    }
    if notes_view:
        brief["notes"] = notes_view

    # plan: latest plan draft (scale) + the plan asset (PlanEditor sources).
    draft_row = await get_latest_draft(session, project_id)
    plan_asset = (
        await session.execute(
            select(AssetRow)
            .where(
                AssetRow.project_id == project_id,
                AssetRow.role.in_(_BRIEF_PLAN_ASSET_ROLES),
                AssetRow.media_type.like("image/%"),
            )
            .order_by(AssetRow.created_at.desc(), AssetRow.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if draft_row is not None or plan_asset is not None:
        plan: dict[str, Any] = {}
        if plan_asset is not None:
            plan["asset_id"] = plan_asset.id
        plan["scale"] = _brief_scale_view(draft_row.draft_json if draft_row else None)
        brief["plan"] = plan

    rooms, furniture_intents = _brief_scene_sections(
        head_revision.scene_json if head_revision is not None else None
    )
    if rooms:
        brief["rooms"] = rooms
    if furniture_intents:
        brief["furniture_intents"] = furniture_intents

    # renders: strictly this variant (legacy NULL-variant rows excluded).
    # Wide window: the rgb filter runs BEFORE the 12-render cap, so rgb-less
    # rows cannot shrink the visible list while older rgb-ful renders exist.
    render_rows = list(
        (
            await session.execute(
                select(RenderManifestRow)
                .where(
                    RenderManifestRow.project_id == project_id,
                    RenderManifestRow.variant_id == variant.id,
                )
                .order_by(
                    RenderManifestRow.created_at.desc(), RenderManifestRow.id.desc()
                )
                .limit(50)
            )
        ).scalars()
    )
    renders, skipped_renders = _brief_renders_view(render_rows)
    if renders:
        brief["renders"] = renders
    if skipped_renders:
        _append_unique(warnings, _BRIEF_SKIPPED_RENDER_WARNING)

    # photos: newest photo attachments, caption from the asset's original name.
    photo_rows = list(
        (
            await session.execute(
                select(AttachmentRow)
                .where(
                    AttachmentRow.project_id == project_id,
                    AttachmentRow.kind == "photo",
                )
                .order_by(AttachmentRow.created_at.desc(), AttachmentRow.id.desc())
                .limit(12)
            )
        ).scalars()
    )
    photo_asset_ids = [row.asset_id for row in photo_rows if row.asset_id]
    photo_assets: dict[str, AssetRow] = {}
    if photo_asset_ids:
        found = await session.execute(
            select(AssetRow).where(AssetRow.id.in_(photo_asset_ids))
        )
        photo_assets = {asset.id: asset for asset in found.scalars()}
    photos: list[dict[str, Any]] = []
    for row in photo_rows:
        if row.asset_id is None:
            continue  # validated non-null on write; defensive for legacy rows
        view: dict[str, Any] = {"attachment_id": row.id, "asset_id": row.asset_id}
        asset = photo_assets.get(row.asset_id)
        if asset is not None and asset.original_name:
            view["caption"] = asset.original_name
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        if metadata:
            view["mapping"] = metadata
        photos.append(view)
    if photos:
        brief["photos"] = photos

    # style_direction: latest profile row read directly (source_text is not
    # exposed by the list endpoint).
    style_row = (
        await session.execute(
            select(StyleProfileRow)
            .where(StyleProfileRow.project_id == project_id)
            .order_by(StyleProfileRow.created_at.desc(), StyleProfileRow.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if style_row is not None:
        style: dict[str, Any] = {}
        if style_row.source_text:
            style["source_text"] = style_row.source_text
        if style_row.profile_json:
            style["summary"] = style_row.profile_json
        if style_row.source_asset_ids:
            style["reference_asset_ids"] = style_row.source_asset_ids
        if style:
            brief["style_direction"] = style

    budget_items = await list_budget_items(session, project_id, variant.id)
    if budget_items:
        brief["budget"] = _brief_budget_view(budget_items)

    brief["warnings"] = warnings
    return brief


@router.post("/api/v1/projects/{project_id}/scene", status_code=201, dependencies=[Depends(require_csrf)])
async def scene_initialize(project_id: str, scene: Scene, session: DbSession, owner: OwnerSession):
    try:
        revision = await initialize_scene(session, project_id, scene)
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc
    return {"revision_id": revision.id, "content_hash": revision.content_hash, "scene": revision.scene_json}


@router.get("/api/v1/projects/{project_id}/scene", dependencies=[Depends(require_owner)])
async def scene_get(project_id: str, session: DbSession):
    revision = await latest_revision(session, project_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="scene not initialized")
    return {
        "revision_id": revision.id,
        "parent_revision_id": revision.parent_revision_id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
    }


@router.get(
    "/api/v1/projects/{project_id}/scene/revisions",
    dependencies=[Depends(require_owner)],
)
async def scene_revisions(project_id: str, session: DbSession):
    rows = await list_revisions(session, project_id)
    return [
        {
            "revision_id": row.id,
            "parent_revision_id": row.parent_revision_id,
            "command_id": row.command_id,
            "content_hash": row.content_hash,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post(
    "/api/v1/projects/{project_id}/scene/revert",
    dependencies=[Depends(require_csrf)],
)
async def scene_revert(
    project_id: str,
    payload: SceneRevert,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        revision = await revert_scene(
            session,
            project_id,
            expected_base_revision_id=payload.expected_base_revision_id,
            target_revision_id=payload.target_revision_id,
        )
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc
    return {
        "revision_id": revision.id,
        "parent_revision_id": revision.parent_revision_id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
    }


@router.post("/api/v1/projects/{project_id}/scene/commands", dependencies=[Depends(require_csrf)])
async def scene_command(
    project_id: str,
    command: DesignCommand,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        revision = await apply_scene_command(
            session,
            project_id,
            command,
            correlation_id=request.state.request_id,
        )
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc
    return {
        "revision_id": revision.id,
        "parent_revision_id": revision.parent_revision_id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
    }


@router.get(
    "/api/v1/projects/{project_id}/cameras",
    dependencies=[Depends(require_owner)],
)
async def camera_list(project_id: str, session: DbSession):
    revision = await latest_revision(session, project_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="scene not initialized")
    scene = Scene.model_validate(revision.scene_json)
    return {
        "revision_id": revision.id,
        "cameras": [
            camera.model_dump(mode="json", exclude_none=True)
            for camera in scene.cameras
        ],
    }


@router.get(
    "/api/v1/projects/{project_id}/cameras/{camera_id}",
    dependencies=[Depends(require_owner)],
)
async def camera_get(project_id: str, camera_id: str, session: DbSession):
    revision = await latest_revision(session, project_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="scene not initialized")
    scene = Scene.model_validate(revision.scene_json)
    camera = next((item for item in scene.cameras if item.id == camera_id), None)
    if camera is None:
        raise HTTPException(status_code=404, detail="camera not found")
    return {
        "revision_id": revision.id,
        "camera": camera.model_dump(mode="json", exclude_none=True),
    }


@router.put(
    "/api/v1/projects/{project_id}/cameras/{camera_id}",
    dependencies=[Depends(require_csrf)],
)
async def camera_upsert(
    project_id: str,
    camera_id: str,
    payload: CameraUpsertRequest,
    session: DbSession,
    owner: OwnerSession,
):
    if payload.camera.id != camera_id:
        raise HTTPException(status_code=422, detail="camera ID does not match route")
    try:
        revision = await upsert_camera(
            session,
            project_id,
            expected_base_revision_id=payload.base_revision_id,
            camera=payload.camera,
            solve=payload.solve,
        )
    except (ValueError, CommandRejected, CommandConflict) as exc:
        raise _domain_conflict(exc) from exc
    scene = Scene.model_validate(revision.scene_json)
    camera = next(item for item in scene.cameras if item.id == camera_id)
    return {
        "revision_id": revision.id,
        "content_hash": revision.content_hash,
        "camera": camera.model_dump(mode="json", exclude_none=True),
        "scene": revision.scene_json,
    }


@router.delete(
    "/api/v1/projects/{project_id}/cameras/{camera_id}",
    dependencies=[Depends(require_csrf)],
)
async def camera_delete(
    project_id: str,
    camera_id: str,
    payload: CameraDeleteRequest,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        revision = await remove_camera(
            session,
            project_id,
            expected_base_revision_id=payload.base_revision_id,
            camera_id=camera_id,
        )
    except (ValueError, CommandRejected, CommandConflict) as exc:
        raise _domain_conflict(exc) from exc
    return {
        "revision_id": revision.id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
    }


@router.post("/api/v1/projects/{project_id}/assets", status_code=201, dependencies=[Depends(require_csrf)])
async def asset_upload(
    project_id: str,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
    role: str = Form("apartment"),
    file: UploadFile = File(...),
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if role not in {
        "apartment",
        "plan",
        "photo",
        "reference",
        "derived",
        "attachment",
        # R3: previews are normally created internally by the import flow,
        # but the upload endpoint keeps the role validation consistent.
        "product_preview",
    }:
        raise HTTPException(status_code=422, detail="invalid asset role")
    data, media_type = await _validated_upload(file, request)
    digest = hashlib.sha256(data).hexdigest()
    duplicate_result = await session.execute(
        select(AssetRow)
        .where(AssetRow.project_id == project_id, AssetRow.sha256 == digest)
        .order_by(AssetRow.created_at.asc())
        .limit(1)
    )
    duplicate = duplicate_result.scalar_one_or_none()
    suffix = Path(file.filename or "").suffix[:16]
    object_key = f"projects/{project_id}/{uuid4()}{suffix}"
    await request.app.state.object_store.put_bytes(object_key, data, media_type)
    row = AssetRow(
        project_id=project_id,
        object_key=object_key,
        original_name=(file.filename or "")[:255] or None,
        media_type=media_type,
        size_bytes=len(data),
        sha256=digest,
        role=role,
        metadata_json=extract_asset_metadata(data, media_type),
        duplicate_of_asset_id=duplicate.id if duplicate else None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {
        "id": row.id,
        "media_type": row.media_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "role": row.role,
        "metadata": row.metadata_json,
        "duplicate_of_asset_id": row.duplicate_of_asset_id,
    }


@router.get(
    "/api/v1/projects/{project_id}/assets",
    dependencies=[Depends(require_owner)],
)
async def asset_list(project_id: str, session: DbSession):
    result = await session.execute(
        select(AssetRow)
        .where(AssetRow.project_id == project_id)
        .order_by(AssetRow.created_at.desc())
    )
    return [
        {
            "id": row.id,
            "original_name": row.original_name,
            "media_type": row.media_type,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "provenance": row.provenance,
            "role": row.role,
            "metadata": row.metadata_json,
            "source_asset_id": row.source_asset_id,
            "source_asset_ids": row.source_asset_ids,
            "duplicate_of_asset_id": row.duplicate_of_asset_id,
            "created_at": row.created_at,
        }
        for row in result.scalars()
    ]


@router.get("/api/v1/assets/{asset_id}", dependencies=[Depends(require_owner)])
async def asset_download(asset_id: str, request: Request, session: DbSession):
    row = await session.get(AssetRow, asset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="asset not found")
    data = await request.app.state.object_store.get_bytes(row.object_key)
    return Response(
        content=data,
        media_type=row.media_type,
        headers={"Cache-Control": "private, max-age=60"},
    )


def _attachment_view(row: AttachmentRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "kind": row.kind,
        "asset_id": row.asset_id,
        "body": row.body,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "done": row.done,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def _load_scene_entities(
    session: AsyncSession, project_id: str
) -> list[SceneEntity]:
    """Return the entities of the project's current scene (may be empty)."""
    current = await latest_revision(session, project_id)
    if current is None:
        return []
    scene = Scene.model_validate(current.scene_json)
    return scene.entities


async def _load_current_scene(
    session: AsyncSession, project_id: str
) -> Scene | None:
    """Return the project's current scene (None when never initialized)."""
    current = await latest_revision(session, project_id)
    if current is None:
        return None
    return Scene.model_validate(current.scene_json)


# R7 photo→room mapping metadata (#173). Validated only for photo attachments
# carrying a non-empty metadata object; the R6 minimal stamp
# {mapping: "owner_room", confidence: "approx"} stays valid as-is.
_PHOTO_MAPPING_VALUES = frozenset({"owner_room"})
_PHOTO_MAPPING_CONFIDENCES = frozenset({"approx", "confirmed", "calibrated"})
_PHOTO_MAPPING_SOURCES = frozenset({"user", "estimated", "model_inferred"})
_VISIBLE_TARGET_KINDS = frozenset({"wall", "door", "window", "floor", "ceiling"})
_ORIENTATION_LOOKS_AT = frozenset({"wall", "window", "door", "corner"})
_ORIENTATION_FROM = frozenset({"corner", "center"})


def _invalid_photo_mapping(detail: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "invalid_photo_mapping_metadata", "detail": detail},
    )


def _unknown_visible_target(target_id: object) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "unknown_visible_target", "target_id": target_id},
    )


async def _validate_photo_mapping_metadata(
    session: AsyncSession,
    project_id: str,
    *,
    kind: str,
    asset_id: str | None,
    metadata: object,
) -> None:
    """Structured validation of photo attachment mapping metadata (R7 #173).

    Unknown top-level metadata keys are deliberately ignored: the R1
    attachment metadata dict stays open for non-mapping uses (e.g. the R6
    walkthrough stamp), and only the photo-mapping keys below carry
    structural rules.
    """
    if kind != "photo" or not isinstance(metadata, dict) or not metadata:
        return

    mapping = metadata.get("mapping")
    if "mapping" in metadata and mapping not in _PHOTO_MAPPING_VALUES:
        raise _invalid_photo_mapping("mapping must be 'owner_room'")

    confidence = metadata.get("confidence")
    if "confidence" in metadata and confidence not in _PHOTO_MAPPING_CONFIDENCES:
        raise _invalid_photo_mapping(
            "confidence must be one of: approx, confirmed, calibrated"
        )

    scene: Scene | None = None

    visible_targets = metadata.get("visible_targets")
    if "visible_targets" in metadata:
        if not isinstance(visible_targets, list):
            raise _invalid_photo_mapping("visible_targets must be a list")
        if visible_targets:
            scene = await _load_current_scene(session, project_id)
            entities = scene.entities if scene is not None else []
            for entry in visible_targets:
                if (
                    not isinstance(entry, dict)
                    or entry.get("target_type") != "entity"
                    or not isinstance(entry.get("target_id"), str)
                    or not entry["target_id"]
                ):
                    raise _invalid_photo_mapping(
                        "visible_targets entries require target_type='entity' "
                        "and a non-empty target_id"
                    )
                target_id = entry["target_id"]
                entity = next(
                    (item for item in entities if item.id == target_id), None
                )
                entry_kind = entry.get("kind")
                if entity is None or entry_kind not in _VISIBLE_TARGET_KINDS:
                    raise _unknown_visible_target(target_id)
                if entity.kind.value != entry_kind:
                    raise _unknown_visible_target(target_id)

    orientation_hint = metadata.get("orientation_hint")
    if "orientation_hint" in metadata:
        if not isinstance(orientation_hint, dict):
            raise _invalid_photo_mapping("orientation_hint must be an object")
        looks_at = orientation_hint.get("looks_at")
        if looks_at is not None and looks_at not in _ORIENTATION_LOOKS_AT:
            raise _invalid_photo_mapping(
                "orientation_hint.looks_at must be one of: wall, window, door, corner"
            )
        origin = orientation_hint.get("from")
        if origin is not None and origin not in _ORIENTATION_FROM:
            raise _invalid_photo_mapping(
                "orientation_hint.from must be one of: corner, center"
            )
        owner_label = orientation_hint.get("owner_label")
        if owner_label is not None and not isinstance(owner_label, str):
            raise _invalid_photo_mapping(
                "orientation_hint.owner_label must be a string"
            )

    camera_id = metadata.get("camera_id")
    if "camera_id" in metadata:
        if not isinstance(camera_id, str) or not camera_id:
            raise _invalid_photo_mapping("camera_id must be a non-empty string")
        if scene is None:
            scene = await _load_current_scene(session, project_id)
        camera = next(
            (item for item in (scene.cameras if scene else []) if item.id == camera_id),
            None,
        )
        # The camera must exist in the latest revision and be calibrated from
        # this very photo asset; anything else is a camera/asset mismatch.
        if camera is None or camera.source_asset_id != asset_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "camera_asset_mismatch",
                    "camera_id": camera_id,
                },
            )

    provenance = metadata.get("provenance")
    if "provenance" in metadata:
        if not isinstance(provenance, dict):
            raise _invalid_photo_mapping("provenance must be an object")
        if provenance.get("source") not in _PHOTO_MAPPING_SOURCES:
            raise _invalid_photo_mapping(
                "provenance.source must be one of: user, estimated, model_inferred"
            )
        note = provenance.get("note")
        if note is not None and not isinstance(note, str):
            raise _invalid_photo_mapping("provenance.note must be a string")


@router.get(
    "/api/v1/projects/{project_id}/attachments",
    dependencies=[Depends(require_owner)],
)
async def attachment_list(
    project_id: str,
    session: DbSession,
    target_type: str | None = None,
    target_id: str | None = None,
    kind: str | None = None,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    # Dangling targets are intentional: a plan recommit can replace the scene
    # and retire entity ids, but the attachment (a pin/measure note) survives.
    # The list returns rows as-is; clients flag dangling targets themselves.
    query = select(AttachmentRow).where(AttachmentRow.project_id == project_id)
    if target_type is not None:
        query = query.where(AttachmentRow.target_type == target_type)
    if target_id is not None:
        query = query.where(AttachmentRow.target_id == target_id)
    if kind is not None:
        query = query.where(AttachmentRow.kind == kind)
    result = await session.execute(query.order_by(AttachmentRow.created_at.desc()))
    return [_attachment_view(row) for row in result.scalars()]


@router.post(
    "/api/v1/projects/{project_id}/attachments",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def attachment_create(
    project_id: str,
    payload: AttachmentCreate,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    if payload.asset_id is not None:
        asset = await session.get(AssetRow, payload.asset_id)
        if asset is None or asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_attachment_asset",
                    "asset_id": payload.asset_id,
                },
            )

    if payload.target_type in {"room", "entity"}:
        entities = await _load_scene_entities(session, project_id)
        target = next(
            (entity for entity in entities if entity.id == payload.target_id),
            None,
        )
        if target is None:
            code = "unknown_room" if payload.target_type == "room" else "unknown_entity"
            raise HTTPException(
                status_code=422,
                detail={"code": code, "entity_id": payload.target_id},
            )
        if payload.target_type == "room" and target.kind.value != "room":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_room_target",
                    "detail": "target must be a room-kind entity",
                    "entity_id": payload.target_id,
                },
            )

    # R7 #173: photo attachments may carry structured photo→room mapping
    # metadata; validate it against the current scene before persisting.
    await _validate_photo_mapping_metadata(
        session,
        project_id,
        kind=payload.kind,
        asset_id=payload.asset_id,
        metadata=payload.metadata,
    )

    row = AttachmentRow(
        project_id=project_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        kind=payload.kind,
        asset_id=payload.asset_id,
        body=payload.body,
        due_date=payload.due_date,
        metadata_json=payload.metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _attachment_view(row)


@router.patch(
    "/api/v1/projects/{project_id}/attachments/{attachment_id}",
    dependencies=[Depends(require_csrf)],
)
async def attachment_update(
    project_id: str,
    attachment_id: str,
    payload: AttachmentPatch,
    session: DbSession,
    owner: OwnerSession,
):
    row = await session.get(AttachmentRow, attachment_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="attachment not found")
    changes = payload.model_dump(exclude_unset=True)
    if "metadata" in changes:
        # R7 #173: the kind/asset are immutable, so photo-mapping metadata is
        # validated against the attachment's own kind/asset before mutating.
        await _validate_photo_mapping_metadata(
            session,
            project_id,
            kind=row.kind,
            asset_id=row.asset_id,
            metadata=changes["metadata"],
        )
    if "body" in changes:
        row.body = changes["body"]
    if "done" in changes:
        row.done = changes["done"]
    if "due_date" in changes:
        row.due_date = changes["due_date"]
    if "metadata" in changes:
        row.metadata_json = changes["metadata"]
    await session.commit()
    await session.refresh(row)
    return _attachment_view(row)


@router.delete(
    "/api/v1/projects/{project_id}/attachments/{attachment_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def attachment_delete(
    project_id: str,
    attachment_id: str,
    session: DbSession,
    owner: OwnerSession,
):
    row = await session.get(AttachmentRow, attachment_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="attachment not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Product candidates (R3)
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/projects/{project_id}/products/import-url",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def product_import_url(
    project_id: str,
    payload: ProductImportUrlRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    state = request.app.state
    # Test seams: app.state.product_url_extractor / product_image_fetcher
    # override the guard-backed defaults (product API tests).
    extractor = getattr(state, "product_url_extractor", None) or GenericHtmlExtractor()
    image_fetcher = getattr(state, "product_image_fetcher", None)
    try:
        row = await create_from_url(
            session,
            project_id,
            payload.url,
            extractor=extractor,
            object_store=state.object_store,
            image_fetcher=image_fetcher,
        )
    except GuardError as exc:
        # SSRF/transport policy violations are client-visible 422s, never 500.
        raise HTTPException(
            status_code=422,
            detail={"code": "url_rejected", "reason": exc.reason},
        ) from exc
    return {
        "candidate": candidate_view(row),
        "missing_fields": missing_candidate_fields(row),
        "extraction": {
            "status": extraction_status(row.extraction_confidence),
            "confidence": row.extraction_confidence,
        },
    }


@router.post(
    "/api/v1/projects/{project_id}/products",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def product_create(
    project_id: str,
    payload: ProductCandidateCreate,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if payload.source_asset_id is not None:
        asset = await session.get(AssetRow, payload.source_asset_id)
        if asset is None or asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_product_source_asset"},
            )
    row = await create_candidate(
        session,
        project_id,
        title=payload.title,
        brand=payload.brand,
        model=payload.model,
        price=payload.price,
        currency=payload.currency,
        width_mm=payload.width_mm,
        depth_mm=payload.depth_mm,
        height_mm=payload.height_mm,
        material_descriptors=payload.material_descriptors,
        color_descriptors=payload.color_descriptors,
        source_asset_id=payload.source_asset_id,
        three_d_ref=payload.three_d_ref,
    )
    return candidate_view(row)


@router.get(
    "/api/v1/projects/{project_id}/products",
    dependencies=[Depends(require_owner)],
)
async def product_list(
    project_id: str,
    session: DbSession,
    source: str | None = None,
    has_dimensions: bool | None = None,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if source is not None and source not in {"manual", "url"}:
        raise HTTPException(status_code=422, detail="invalid source filter")
    rows = await list_candidates(
        session, project_id, source=source, has_dimensions=has_dimensions
    )
    return [candidate_view(row) for row in rows]


@router.get(
    "/api/v1/projects/{project_id}/products/{candidate_id}",
    dependencies=[Depends(require_owner)],
)
async def product_get(
    project_id: str,
    candidate_id: str,
    session: DbSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    row = await get_candidate(session, project_id, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="product candidate not found")
    return candidate_view(row)


@router.patch(
    "/api/v1/projects/{project_id}/products/{candidate_id}",
    dependencies=[Depends(require_csrf)],
)
async def product_patch(
    project_id: str,
    candidate_id: str,
    payload: ProductCandidatePatch,
    session: DbSession,
    owner: OwnerSession,
):
    row = await get_candidate(session, project_id, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="product candidate not found")
    updated = await patch_candidate(
        session,
        project_id,
        candidate_id,
        payload.model_dump(exclude_unset=True),
    )
    return candidate_view(updated)


@router.delete(
    "/api/v1/projects/{project_id}/products/{candidate_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def product_delete(
    project_id: str,
    candidate_id: str,
    session: DbSession,
    owner: OwnerSession,
):
    deleted = await delete_candidate(session, project_id, candidate_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="product candidate not found")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# scene variants (R4) + variant budget
# ---------------------------------------------------------------------------


def _variant_http_error(exc: VariantError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "detail": exc.detail},
    )


def _budget_http_error(exc: BudgetError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "detail": exc.detail},
    )


async def _resolve_variant_scoped_revision(
    session: AsyncSession,
    project_id: str,
    *,
    variant_id: str,
    revision_id: str | None,
    revision_field: str,
) -> SceneRevisionRow:
    """Resolve the revision a variant-scoped render/generation targets.

    An explicit revision must lie in the variant's lineage (409
    ``revision_not_in_lineage``); without one, the variant HEAD is used —
    the global latest revision is never read for variant-scoped jobs.
    """
    variant = await get_variant(session, project_id, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="variant not found")
    if revision_id is not None:
        revision = await session.get(SceneRevisionRow, revision_id)
        if revision is None or revision.project_id != project_id:
            raise HTTPException(
                status_code=404, detail=f"{revision_field} not found"
            )
        if revision_id != variant.head_scene_revision_id:
            lineage = await variant_lineage_ids(
                session, variant.head_scene_revision_id
            )
            if revision_id not in lineage:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "revision_not_in_lineage",
                        "detail": (
                            f"revision {revision_id} is not in the lineage "
                            f"of variant {variant_id}"
                        ),
                    },
                )
        return revision
    revision = await session.get(SceneRevisionRow, variant.head_scene_revision_id)
    if revision is None:
        raise HTTPException(status_code=409, detail="scene is not initialized")
    return revision


@router.post(
    "/api/v1/projects/{project_id}/variants:create-from-current",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def variant_create_from_current(
    project_id: str,
    payload: VariantCreateFromCurrent,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        row = await create_variant_from_current(session, project_id, payload.title)
    except VariantError as exc:
        raise _variant_http_error(exc) from exc
    except (ValueError, CommandRejected) as exc:
        # Uninitialized scene → 409 via the shared domain mapping.
        raise _domain_conflict(exc) from exc
    return variant_view(row)


@router.get(
    "/api/v1/projects/{project_id}/variants",
    dependencies=[Depends(require_owner)],
)
async def variant_list(
    project_id: str,
    session: DbSession,
    status: str | None = None,
    include_archived: bool = False,
):
    rows = await list_variants(
        session, project_id, status=status, include_archived=include_archived
    )
    return [variant_view(row) for row in rows]


@router.get(
    "/api/v1/projects/{project_id}/variants/compare",
    dependencies=[Depends(require_owner)],
)
async def variants_compare(
    project_id: str,
    session: DbSession,
    left: str = Query(min_length=1),
    right: str = Query(min_length=1),
):
    # NOTE: registered before /variants/{variant_id} so "compare" is never
    # captured as a variant id.
    try:
        return await compare_variants(session, project_id, left, right)
    except VariantError as exc:
        raise _variant_http_error(exc) from exc


@router.get(
    "/api/v1/projects/{project_id}/variants/{variant_id}",
    dependencies=[Depends(require_owner)],
)
async def variant_get(project_id: str, variant_id: str, session: DbSession):
    row = await get_variant(session, project_id, variant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="variant not found")
    return variant_view(row)


@router.patch(
    "/api/v1/projects/{project_id}/variants/{variant_id}",
    dependencies=[Depends(require_csrf)],
)
async def variant_patch(
    project_id: str,
    variant_id: str,
    payload: VariantPatch,
    session: DbSession,
    owner: OwnerSession,
):
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    try:
        row = await patch_variant(session, project_id, variant_id, updates)
    except VariantError as exc:
        raise _variant_http_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="variant not found")
    return variant_view(row)


@router.delete(
    "/api/v1/projects/{project_id}/variants/{variant_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def variant_delete(
    project_id: str, variant_id: str, session: DbSession, owner: OwnerSession
):
    deleted = await delete_variant(session, project_id, variant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="variant not found")
    return Response(status_code=204)


@router.post(
    "/api/v1/projects/{project_id}/variants/{variant_id}:fork",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def variant_fork(
    project_id: str,
    variant_id: str,
    payload: VariantForkRequest,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        row = await fork_variant(
            session,
            project_id,
            variant_id,
            title=payload.title,
            from_revision_id=payload.from_revision_id,
        )
    except VariantError as exc:
        raise _variant_http_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="variant not found")
    return variant_view(row)


@router.post(
    "/api/v1/projects/{project_id}/variants/{variant_id}/revisions:restore",
    dependencies=[Depends(require_csrf)],
)
async def variant_restore_revision(
    project_id: str,
    variant_id: str,
    payload: VariantRestoreRevision,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        revision = await restore_variant_revision(
            session,
            project_id,
            variant_id,
            target_revision_id=payload.target_revision_id,
            expected_head_revision_id=payload.expected_head_revision_id,
        )
    except VariantError as exc:
        raise _variant_http_error(exc) from exc
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc
    return {
        "revision_id": revision.id,
        "parent_revision_id": revision.parent_revision_id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
    }


@router.post(
    "/api/v1/projects/{project_id}/variants/{variant_id}/revisions",
    dependencies=[Depends(require_csrf)],
)
async def variant_command(
    project_id: str,
    variant_id: str,
    payload: VariantCommandRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    # Variant-scoped application reusing the existing command engine: same
    # ops, same locks/intents. The canonical scene is never read or written.
    try:
        revision = await apply_variant_command(
            session,
            project_id,
            variant_id,
            payload.command,
            expected_head_revision_id=payload.expected_head_revision_id,
            correlation_id=request.state.request_id,
        )
    except VariantError as exc:
        raise _variant_http_error(exc) from exc
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc
    return {
        "revision_id": revision.id,
        "parent_revision_id": revision.parent_revision_id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
        "variant_head_scene_revision_id": revision.id,
    }


@router.post(
    "/api/v1/projects/{project_id}/variants/{variant_id}/budget/items",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def budget_item_create(
    project_id: str,
    variant_id: str,
    payload: BudgetItemCreate,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        row = await add_budget_item(
            session, project_id, variant_id, payload.model_dump(exclude_none=True)
        )
    except BudgetError as exc:
        raise _budget_http_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="variant not found")
    return budget_item_view(row)


@router.get(
    "/api/v1/projects/{project_id}/variants/{variant_id}/budget/items",
    dependencies=[Depends(require_owner)],
)
async def budget_item_list(
    project_id: str, variant_id: str, session: DbSession
):
    if await get_variant(session, project_id, variant_id) is None:
        raise HTTPException(status_code=404, detail="variant not found")
    rows = await list_budget_items(session, project_id, variant_id)
    return [budget_item_view(row) for row in rows]


@router.get(
    "/api/v1/projects/{project_id}/variants/{variant_id}/budget/report",
    dependencies=[Depends(require_owner)],
)
async def budget_item_report(
    project_id: str, variant_id: str, session: DbSession
):
    try:
        return await budget_report(session, project_id, variant_id)
    except BudgetError as exc:
        raise _budget_http_error(exc) from exc


@router.patch(
    "/api/v1/projects/{project_id}/variants/{variant_id}/budget/items/{item_id}",
    dependencies=[Depends(require_csrf)],
)
async def budget_item_update(
    project_id: str,
    variant_id: str,
    item_id: str,
    payload: BudgetItemPatch,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        row = await patch_budget_item(
            session,
            project_id,
            variant_id,
            item_id,
            payload.model_dump(exclude_unset=True),
        )
    except BudgetError as exc:
        raise _budget_http_error(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="budget item not found")
    return budget_item_view(row)


@router.delete(
    "/api/v1/projects/{project_id}/variants/{variant_id}/budget/items/{item_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
)
async def budget_item_delete(
    project_id: str,
    variant_id: str,
    item_id: str,
    session: DbSession,
    owner: OwnerSession,
):
    try:
        deleted = await delete_budget_item(session, project_id, variant_id, item_id)
    except BudgetError as exc:
        raise _budget_http_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="budget item not found")
    return Response(status_code=204)


@router.post(
    "/api/v1/projects/{project_id}/geometry-diagnostics",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def geometry_diagnostic_create(
    project_id: str,
    payload: GeometryDiagnosticRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    revision = await session.get(SceneRevisionRow, payload.scene_revision_id)
    if revision is None or revision.project_id != project_id:
        raise HTTPException(status_code=404, detail="scene revision not found")
    scene = Scene.model_validate(revision.scene_json)
    if not any(camera.id == payload.camera_id for camera in scene.cameras):
        raise HTTPException(status_code=422, detail={"code": "unknown_camera"})

    input_ids = [payload.reference_asset_id, payload.generated_asset_id]
    if payload.protected_mask_asset_id:
        input_ids.append(payload.protected_mask_asset_id)
    for asset_id in input_ids:
        asset = await session.get(AssetRow, asset_id)
        if asset is None or asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_diagnostic_asset", "asset_id": asset_id},
            )
        if not asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail={"code": "diagnostic_asset_must_be_image", "asset_id": asset_id},
            )

    row = await create_job(
        session,
        project_id=project_id,
        job_type="quality.geometry_check",
        required_capabilities=["geometry_quality"],
        payload={
            "diagnostic_id": str(uuid4()),
            "scene_revision_id": revision.id,
            "camera_id": payload.camera_id,
            "reference_asset_id": payload.reference_asset_id,
            "generated_asset_id": payload.generated_asset_id,
            "protected_mask_asset_id": payload.protected_mask_asset_id,
            "input_asset_ids": input_ids,
            "tolerance_px": payload.tolerance_px,
            "edge_threshold": payload.edge_threshold,
            "advisory_threshold": payload.advisory_threshold,
        },
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
    )
    return job_view(row)


@router.get(
    "/api/v1/projects/{project_id}/geometry-diagnostics",
    dependencies=[Depends(require_owner)],
)
async def geometry_diagnostic_list(project_id: str, session: DbSession):
    rows = await list_geometry_diagnostics(session, project_id)
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "created_at": row.created_at,
            "diagnostic": row.diagnostic_json,
        }
        for row in rows
    ]


@router.get(
    "/api/v1/geometry-diagnostics/{diagnostic_id}",
    dependencies=[Depends(require_owner)],
)
async def geometry_diagnostic_get(diagnostic_id: str, session: DbSession):
    row = await session.get(GeometryDiagnosticRow, diagnostic_id)
    if row is None:
        raise HTTPException(status_code=404, detail="geometry diagnostic not found")
    return {
        "id": row.id,
        "job_id": row.job_id,
        "project_id": row.project_id,
        "created_at": row.created_at,
        "diagnostic": row.diagnostic_json,
    }


@router.post(
    "/api/v1/projects/{project_id}/renders",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def render_create(
    project_id: str,
    payload: RenderRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    if payload.variant_id:
        revision = await _resolve_variant_scoped_revision(
            session,
            project_id,
            variant_id=payload.variant_id,
            revision_id=payload.scene_revision_id,
            revision_field="scene revision",
        )
    elif payload.scene_revision_id:
        revision = await session.get(SceneRevisionRow, payload.scene_revision_id)
        if revision is None or revision.project_id != project_id:
            raise HTTPException(status_code=404, detail="scene revision not found")
    else:
        revision = await latest_revision(session, project_id)
        if revision is None:
            raise HTTPException(status_code=409, detail="scene is not initialized")

    scene = Scene.model_validate(revision.scene_json)
    if not any(camera.id == payload.camera_id for camera in scene.cameras):
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_camera", "camera_id": payload.camera_id},
        )

    # R7: draft renders map to the fast preview profile, but only when the
    # client did not pin renderer_profile explicitly (model_fields_set tells
    # "explicitly given" apart from the field default).
    renderer_profile = payload.renderer_profile
    if "renderer_profile" not in payload.model_fields_set and payload.stage == "draft":
        renderer_profile = "blender-eevee-v0"

    render_id = str(uuid4())
    row = await create_job(
        session,
        project_id=project_id,
        job_type="render.blender",
        required_capabilities=["blender_render"],
        payload={
            "purpose": "render",
            "render_id": render_id,
            "scene_revision_id": revision.id,
            "design_revision_id": payload.design_revision_id,
            "camera_id": payload.camera_id,
            "renderer_profile": renderer_profile,
            "stage": payload.stage,
            "scene": revision.scene_json,
            "variant_id": payload.variant_id,
        },
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
    )
    return job_view(row)


@router.get(
    "/api/v1/projects/{project_id}/renders",
    dependencies=[Depends(require_owner)],
)
async def render_list(project_id: str, session: DbSession):
    rows = await list_render_manifests(session, project_id)
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "scene_revision_id": row.scene_revision_id,
            "design_revision_id": row.design_revision_id,
            "camera_id": row.camera_id,
            "variant_id": row.variant_id,
            "created_at": row.created_at,
            "render_seconds": (row.manifest_json or {}).get("render_seconds"),
            "manifest": row.manifest_json,
        }
        for row in rows
    ]


@router.get(
    "/api/v1/renders/{render_id}",
    dependencies=[Depends(require_owner)],
)
async def render_get(render_id: str, session: DbSession):
    row = await session.get(RenderManifestRow, render_id)
    if row is None:
        raise HTTPException(status_code=404, detail="render not found")
    return {
        "id": row.id,
        "job_id": row.job_id,
        "project_id": row.project_id,
        "scene_revision_id": row.scene_revision_id,
        "design_revision_id": row.design_revision_id,
        "camera_id": row.camera_id,
        "variant_id": row.variant_id,
        "created_at": row.created_at,
        "render_seconds": (row.manifest_json or {}).get("render_seconds"),
        "manifest": row.manifest_json,
    }


@router.post(
    "/api/v1/projects/{project_id}/validation",
    dependencies=[Depends(require_csrf)],
)
async def validation_create(
    project_id: str,
    payload: ValidationRequest,
    session: DbSession,
    owner: OwnerSession,
):
    # Read-only by contract: this endpoint never writes a scene revision.
    try:
        row = await run_validation(
            session,
            project_id,
            scene_revision_id=payload.scene_revision_id,
            min_walkway_mm=payload.min_walkway_mm,
        )
    except ValidationConfigError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_validation_config", "detail": str(exc)},
        ) from exc
    except ValidationRevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return validation_view(row)


@router.get(
    "/api/v1/projects/{project_id}/validation/latest",
    dependencies=[Depends(require_owner)],
)
async def validation_latest(
    project_id: str,
    session: DbSession,
    scene_revision_id: str | None = None,
):
    try:
        row = await latest_validation_report(
            session, project_id, scene_revision_id=scene_revision_id
        )
    except ValidationRevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="validation report not found")
    return validation_view(row)


@router.post(
    "/api/v1/projects/{project_id}/replacements",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def replacement_create(
    project_id: str,
    payload: ReplacementRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    current = await latest_revision(session, project_id)
    if current is None:
        raise HTTPException(status_code=409, detail="scene is not initialized")
    if current.id != payload.base_revision_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "detail": (
                    f"stale base revision: expected {current.id}, "
                    f"got {payload.base_revision_id}"
                ),
            },
        )

    # A reference image is optional: Remove/Restyle photo-first actions carry
    # no reference. When absent the server binds the workflow's reference slot
    # to a synthetic 1x1 black asset (created below, once the base is known)
    # and the IP-Adapter weight is forced to 0.0.
    has_reference = payload.reference_asset_id is not None
    reference: AssetRow | None = None
    if has_reference:
        reference = await session.get(AssetRow, payload.reference_asset_id)
        if reference is None or reference.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_reference_asset",
                    "asset_id": payload.reference_asset_id,
                },
            )
        if reference.role != "reference" or not reference.media_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_reference_asset",
                    "detail": "replacement reference must be an image asset with role=reference",
                },
            )

    scene = Scene.model_validate(current.scene_json)
    # Photo-first (mask_region-only) requests skip canonical scene resolution
    # entirely: no entity/camera lookup, so missing scene objects cannot 422.
    target = None
    if payload.target_entity_id is not None:
        target = next(
            (entity for entity in scene.entities if entity.id == payload.target_entity_id),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_entity", "entity_id": payload.target_entity_id},
            )
        if target.kind.value != "furniture":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_replacement_target",
                    "detail": "replacement target must be furniture",
                },
            )

    camera = None
    if payload.camera_id is not None:
        camera = next(
            (item for item in scene.cameras if item.id == payload.camera_id),
            None,
        )
        if camera is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_camera", "camera_id": payload.camera_id},
            )

    try:
        region = resolve_replacement_region(target, camera, payload.mask_region)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "replacement_region_unavailable", "detail": str(exc)},
        ) from exc

    # If base_asset_id is provided, pin to that asset; otherwise resolve from lineage
    if payload.base_asset_id:
        base_asset = await session.get(AssetRow, payload.base_asset_id)
        if base_asset is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_base_asset", "asset_id": payload.base_asset_id},
            )
        if base_asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_base_asset",
                    "asset_id": payload.base_asset_id,
                    "detail": "base asset belongs to another project",
                },
            )
        if not base_asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_base_asset",
                    "asset_id": payload.base_asset_id,
                    "detail": "base asset must be an image",
                },
            )
        if payload.camera_id is not None:
            # Legacy full requests still guard against pinning a base image
            # from another camera. Photo-first requests carry no camera, so
            # there is no camera identity to verify against.
            result = await session.execute(
                select(GenerationManifestRow).where(
                    GenerationManifestRow.project_id == project_id,
                    GenerationManifestRow.camera_id == payload.camera_id,
                )
            )
            manifests = result.scalars().all()
            same_camera = any(
                base_asset.id in (m.manifest_json.get("output_asset_ids") or [])
                for m in manifests
            )
            if not same_camera:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_base_asset",
                        "asset_id": payload.base_asset_id,
                        "detail": "base asset was rendered from a different camera; pin a base image from the same camera view",
                    },
                )
    else:
        base_asset = await resolve_base_asset_for_edit(
            session, project_id, payload.camera_id, current.id
        )
        if base_asset is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_base_image_available",
                    "detail": "generate this camera view before replacing objects",
                    "camera_id": payload.camera_id,
                },
            )
        if not base_asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_base_image_available",
                    "detail": f"asset {base_asset.id} is not an image",
                    "camera_id": payload.camera_id,
                },
            )

    base_w = base_asset.metadata_json.get("width_px")
    base_h = base_asset.metadata_json.get("height_px")
    if base_w is None or base_h is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "unsupported_base_size", "detail": "base asset missing dimensions"},
        )

    base_image_bytes: bytes | None = None
    if payload.shape == "silhouette":
        # Only the silhouette path needs pixel access; the rectangle mask is
        # geometry-only. A missing/unreadable base asset degrades to fallback.
        try:
            base_image_bytes = await request.app.state.object_store.get_bytes(
                base_asset.object_key
            )
        except Exception:  # noqa: BLE001 - fall back to the rectangle mask
            base_image_bytes = None

    try:
        if region.type == "client_override":
            # mask_region is already in base-image pixel space; identity rescale.
            # Offload to a thread: silhouette rendering runs rembg/U2Net CPU
            # inference and must not block the event loop.
            mask_bytes = await asyncio.to_thread(
                render_replacement_mask,
                region,
                base_w,
                base_h,
                base_w,
                base_h,
                shape=payload.shape,
                base_image=base_image_bytes,
            )
        else:
            mask_bytes = await asyncio.to_thread(
                render_replacement_mask,
                region,
                camera.width_px if camera is not None else base_w,
                camera.height_px if camera is not None else base_h,
                base_w,
                base_h,
                shape=payload.shape,
                base_image=base_image_bytes,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "mask_degenerate", "detail": str(exc)},
        ) from exc

    mask_filename = f"replacement-mask-{target.id if target is not None else 'region'}.png"
    object_key = f"projects/{project_id}/{uuid4()}.png"
    await request.app.state.object_store.put_bytes(
        object_key, mask_bytes, "image/png"
    )
    mask_asset = AssetRow(
        id=str(uuid4()),
        project_id=project_id,
        object_key=object_key,
        original_name=mask_filename,
        media_type="image/png",
        size_bytes=len(mask_bytes),
        sha256=hashlib.sha256(mask_bytes).hexdigest(),
        provenance="generated",
        role="mask",
        source_asset_ids=[base_asset.id],
        metadata_json={
            "width_px": base_w,
            "height_px": base_h,
            "target_entity_id": target.id if target is not None else None,
            "bbox_px": region.bbox_px,
            "feather_px": region.feather_px,
            "source": "replacement_mask",
        },
    )
    session.add(mask_asset)
    await session.commit()
    await session.refresh(mask_asset)

    if not has_reference:
        # Bind the workflow's reference slot to the same synthetic 1x1 black
        # asset the redesign path uses; the IP-Adapter weight is forced to 0.0
        # in queue_reference_edit, so the placeholder contributes nothing.
        reference = await create_black_reference_asset(
            session,
            object_store=request.app.state.object_store,
            project_id=project_id,
            base_asset_id=base_asset.id,
            source="replacement_black_reference",
            original_name="replacement-black-reference.png",
        )

    if target is not None:
        # Canonical-scene replacement: mutate the scene and record the command.
        command = DesignCommand(
            command_id=str(uuid4()),
            base_revision_id=current.id,
            operation="replace_object_from_reference",
            target_id=target.id,
            parameters={},
            reference_asset_ids=[reference.id],
            origin="user",
            request_text=payload.prompt,
        )
        try:
            revision = await apply_scene_command(
                session,
                project_id,
                command,
                correlation_id=request.state.request_id,
            )
        except (ValueError, CommandRejected) as exc:
            raise _domain_conflict(exc) from exc

        next_scene = Scene.model_validate(revision.scene_json)
        next_target = next(
            entity for entity in next_scene.entities if entity.id == target.id
        )
        next_camera = next(
            camera for camera in next_scene.cameras if camera.id == payload.camera_id
        )
        command_view: dict[str, Any] | None = command.model_dump(
            mode="json", exclude_none=True
        )
        queue_target_entity_id: str | None = next_target.id
        queue_camera_id: str | None = next_camera.id
    else:
        # Photo-first replacement: there is no canonical entity/camera to
        # mutate. Record lineage with a no-op revision (no DesignCommandRow)
        # and queue a client-mask edit against the pinned base image.
        try:
            revision = await create_noop_revision(
                session,
                project_id,
                parent_revision_id=current.id,
                scene=scene,
            )
        except (ValueError, CommandRejected) as exc:
            raise _domain_conflict(exc) from exc
        next_scene = scene
        command_view = None
        queue_target_entity_id = None
        queue_camera_id = None

    protected_entity_ids = _protected_entity_ids(next_scene, include_shell=True)
    edit_job = await queue_reference_edit(
        session,
        project_id=project_id,
        design_revision_id=revision.id,
        camera_id=queue_camera_id,
        request_text=payload.prompt,
        target_entity_id=queue_target_entity_id,
        reference_asset_id=reference.id,
        affected_region=region.model_dump(mode="json"),
        protected_entity_ids=protected_entity_ids,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
        base_asset_id=base_asset.id,
        mask_asset_id=mask_asset.id,
        reference_subject_bbox=payload.reference_subject_bbox,
        ipa_weight=payload.ipa_weight,
        has_reference=has_reference,
    )
    return {
        "revision_id": revision.id,
        "content_hash": revision.content_hash,
        "scene": revision.scene_json,
        "command": command_view,
        "affected_region": region.model_dump(mode="json"),
        "job": job_view(edit_job),
        "base_asset_id": base_asset.id,
        "mask_asset_id": mask_asset.id,
        "reference_asset_id": reference.id,
        # When no reference was supplied the IP-Adapter weight is forced to
        # 0.0 server-side; surface the effective value for the client.
        "ipa_weight": payload.ipa_weight if has_reference else 0.0,
    }


@router.post(
    "/api/v1/projects/{project_id}/redesigns",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def redesign_create(
    project_id: str,
    payload: RedesignRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    current = await latest_revision(session, project_id)
    if current is None:
        raise HTTPException(status_code=409, detail="scene is not initialized")
    if current.id != payload.base_revision_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "detail": (
                    f"stale base revision: expected {current.id}, "
                    f"got {payload.base_revision_id}"
                ),
            },
        )

    # R9: optional editorial mood. Unknown ids are rejected before any side
    # effect; a known preset prepends its direction text to the prompt.
    mood: MoodPreset | None = None
    if payload.mood_id is not None:
        mood = get_mood(payload.mood_id)
        if mood is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_mood", "mood_id": payload.mood_id},
            )
    request_text = (
        compose_redesign_request_text(payload.prompt, mood)
        if mood is not None
        else payload.prompt
    )

    # Resolve the base photo: an explicit base_asset_id pins the uploaded
    # photo; otherwise fall back to the lineage walk (no camera filter:
    # photo-first).
    if payload.base_asset_id is not None:
        base_asset = await session.get(AssetRow, payload.base_asset_id)
        if base_asset is None or base_asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_base_asset",
                    "asset_id": payload.base_asset_id,
                },
            )
        if not base_asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_base_asset",
                    "asset_id": payload.base_asset_id,
                    "detail": "base asset must be an image",
                },
            )
    else:
        base_asset = await resolve_base_asset_for_edit(
            session, project_id, None, current.id
        )
        if base_asset is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_base_image_available",
                    "detail": "upload or generate a room photo before redesigning",
                },
            )
        if not base_asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_base_image_available",
                    "detail": f"asset {base_asset.id} is not an image",
                },
            )

    reference_asset: AssetRow | None = None
    if payload.reference_asset_id is not None:
        reference_asset = await session.get(AssetRow, payload.reference_asset_id)
        if reference_asset is None or reference_asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_reference_asset",
                    "asset_id": payload.reference_asset_id,
                },
            )
        if reference_asset.role != "reference" or not reference_asset.media_type.startswith(
            "image/"
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_reference_asset",
                    "detail": "redesign reference must be an image asset with role=reference",
                },
            )
    else:
        # Prompt-only restyle: store a synthetic 1x1 black reference so the
        # graph's reference_image slot stays bound; ipa weight is forced 0.0.
        reference_asset = await create_black_reference_asset(
            session,
            object_store=request.app.state.object_store,
            project_id=project_id,
            base_asset_id=base_asset.id,
            source="redesign_black_reference",
            original_name="redesign-black-reference.png",
        )

    scene = Scene.model_validate(current.scene_json)
    try:
        revision = await create_noop_revision(
            session,
            project_id,
            parent_revision_id=current.id,
            scene=scene,
        )
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc

    redesign_job = await queue_reference_redesign(
        session,
        project_id=project_id,
        design_revision_id=revision.id,
        request_text=request_text,
        base_asset_id=base_asset.id,
        reference_asset_id=reference_asset.id,
        strength=payload.strength,
        negative_prompt=payload.negative_prompt,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
        seed=payload.seed,
        has_reference=payload.reference_asset_id is not None,
        mood_id=mood.id if mood is not None else None,
        mood_title=mood.title if mood is not None else None,
    )
    return {
        "revision_id": revision.id,
        "content_hash": revision.content_hash,
        "job": job_view(redesign_job),
        "base_asset_id": base_asset.id,
        "reference_asset_id": reference_asset.id,
    }


@router.post(
    "/api/v1/projects/{project_id}/generations",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def generation_create(
    project_id: str,
    payload: GenerationRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    if payload.variant_id:
        revision = await _resolve_variant_scoped_revision(
            session,
            project_id,
            variant_id=payload.variant_id,
            revision_id=payload.design_revision_id,
            revision_field="design revision",
        )
    elif payload.design_revision_id:
        revision = await session.get(SceneRevisionRow, payload.design_revision_id)
        if revision is None or revision.project_id != project_id:
            raise HTTPException(status_code=404, detail="design revision not found")
    else:
        revision = await latest_revision(session, project_id)
        if revision is None:
            raise HTTPException(status_code=409, detail="scene is not initialized")

    scene = Scene.model_validate(revision.scene_json)
    if not any(camera.id == payload.camera_id for camera in scene.cameras):
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_camera", "camera_id": payload.camera_id},
        )

    for asset_id in payload.reference_asset_ids:
        asset = await session.get(AssetRow, asset_id)
        if asset is None or asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_generation_asset", "asset_id": asset_id},
            )

    protected_entity_ids = _protected_entity_ids(scene, include_shell=False)
    row = await queue_design_generation(
        session,
        project_id=project_id,
        design_revision_id=revision.id,
        camera_id=payload.camera_id,
        request_text=payload.prompt,
        affected_entity_ids=[],
        protected_entity_ids=protected_entity_ids,
        reference_asset_ids=payload.reference_asset_ids,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
        variant_id=payload.variant_id,
    )
    return job_view(row)


@router.get(
    "/api/v1/projects/{project_id}/generations",
    dependencies=[Depends(require_owner)],
)
async def generation_list(project_id: str, session: DbSession):
    rows = await list_generation_manifests(session, project_id)
    return [
        {
            "id": row.id,
            "job_id": row.job_id,
            "scene_revision_id": row.scene_revision_id,
            "design_revision_id": row.design_revision_id,
            "camera_id": row.camera_id,
            "variant_id": row.variant_id,
            "created_at": row.created_at,
            "manifest": row.manifest_json,
        }
        for row in rows
    ]


@router.get(
    "/api/v1/generations/{generation_id}",
    dependencies=[Depends(require_owner)],
)
async def generation_get(generation_id: str, session: DbSession):
    row = await session.get(GenerationManifestRow, generation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return {
        "id": row.id,
        "job_id": row.job_id,
        "project_id": row.project_id,
        "scene_revision_id": row.scene_revision_id,
        "design_revision_id": row.design_revision_id,
        "camera_id": row.camera_id,
        "variant_id": row.variant_id,
        "created_at": row.created_at,
        "manifest": row.manifest_json,
    }


@router.post(
    "/api/v1/projects/{project_id}/style-profiles/analyze",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def style_profile_analyze(
    project_id: str,
    payload: StyleAnalyzeRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    source_assets: list[AssetRow] = []
    for asset_id in payload.reference_asset_ids:
        asset = await session.get(AssetRow, asset_id)
        if asset is None or asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_style_reference",
                    "asset_id": asset_id,
                },
            )
        if asset.role != "reference" or not asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_style_reference",
                    "asset_id": asset_id,
                    "detail": "style references must be image assets with role=reference",
                },
            )
        source_assets.append(asset)

    row = await create_job(
        session,
        project_id=project_id,
        job_type="style.analyze",
        required_capabilities=["style_analysis"],
        payload={
            "purpose": "style_profile",
            "source_text": payload.source_text,
            "input_asset_ids": [asset.id for asset in source_assets],
            "overrides": payload.overrides,
            "model_profile": request.app.state.settings.llm_model_profile,
        },
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
    )
    return job_view(row)


@router.get(
    "/api/v1/projects/{project_id}/style-profiles",
    dependencies=[Depends(require_owner)],
)
async def style_profile_list(project_id: str, session: DbSession):
    rows = await list_style_profiles(session, project_id)
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "model_profile": row.model_profile,
            "correlation_id": row.correlation_id,
            "created_at": row.created_at,
            "profile": row.profile_json,
        }
        for row in rows
    ]


@router.get(
    "/api/v1/style-profiles/{style_profile_id}",
    dependencies=[Depends(require_owner)],
)
async def style_profile_get(style_profile_id: str, session: DbSession):
    row = await session.get(StyleProfileRow, style_profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="style profile not found")
    return {
        "id": row.id,
        "project_id": row.project_id,
        "model_profile": row.model_profile,
        "correlation_id": row.correlation_id,
        "created_at": row.created_at,
        "profile": row.profile_json,
    }


@router.post(
    "/api/v1/projects/{project_id}/plan/analyze",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def plan_analyze(
    project_id: str,
    payload: PlanAnalyzeRequest,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")

    plan_assets: list[AssetRow] = []
    for asset_id in payload.asset_ids:
        asset = await session.get(AssetRow, asset_id)
        if asset is None or asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_plan_asset", "asset_id": asset_id},
            )
        if not asset.media_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_plan_asset",
                    "asset_id": asset_id,
                    "detail": "plan assets must be images",
                },
            )
        plan_assets.append(asset)

    row = await create_job(
        session,
        project_id=project_id,
        job_type="plan.analyze",
        required_capabilities=["plan_analyze"],
        payload={
            "purpose": "plan_draft",
            "input_asset_ids": [asset.id for asset in plan_assets],
            "hints": payload.hints.model_dump(exclude_none=True),
        },
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
    )
    return {**job_view(row), "job_id": row.id, "draft_id": None}


@router.get(
    "/api/v1/projects/{project_id}/plan/draft",
    dependencies=[Depends(require_owner)],
)
async def plan_draft_get(project_id: str, session: DbSession):
    row = await get_latest_draft(session, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="plan draft not found")
    return {
        "draft_id": row.id,
        "project_id": row.project_id,
        "version": row.version,
        "status": row.status,
        "job_id": row.job_id,
        "created_at": row.created_at,
        "draft": row.draft_json,
    }


@router.put(
    "/api/v1/projects/{project_id}/plan/draft",
    dependencies=[Depends(require_csrf)],
)
async def plan_draft_save(
    project_id: str,
    payload: PlanDraftSave,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    row = await save_draft(session, project_id, payload.draft)
    return {
        "draft_id": row.id,
        "project_id": row.project_id,
        "version": row.version,
        "status": row.status,
    }


@router.post(
    "/api/v1/projects/{project_id}/plan/draft/commit",
    dependencies=[Depends(require_csrf)],
)
async def plan_draft_commit(
    project_id: str,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    draft_row = await get_latest_draft(session, project_id)
    if draft_row is None:
        raise HTTPException(status_code=404, detail="plan draft not found")
    try:
        revision = await commit_draft(session, project_id, draft_row)
    except DraftAlreadyCommittedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "draft_already_committed", "detail": str(exc)},
        ) from exc
    except ScaleUnknownError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "scale_unknown", "detail": str(exc)},
        ) from exc
    except (ValueError, CommandRejected) as exc:
        raise _domain_conflict(exc) from exc
    return {"revision_id": revision.id, "content_hash": revision.content_hash}


# ---------------------------------------------------------------------------
# Guided setup (R6, #183)
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/setup",
    dependencies=[Depends(require_owner)],
)
async def project_setup(project_id: str, session: DbSession):
    """Read-only guided-setup snapshot composed from existing project data."""
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await build_setup(session, project_id)


@router.post(
    "/api/v1/projects/{project_id}/design/instructions",
    status_code=201,
    dependencies=[Depends(require_csrf)],
)
async def design_instruction(
    project_id: str,
    payload: DesignInstruction,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    current = await latest_revision(session, project_id)
    if current is None:
        raise HTTPException(status_code=409, detail="scene is not initialized")
    messages: list[dict[str, Any]] = [{"role": "user", "content": payload.text}]
    if payload.selection_context is not None:
        messages = [selection_context_message(payload.selection_context), *messages]
    job_payload: dict[str, Any] = {
        "purpose": "design_instruction",
        "base_revision_id": current.id,
        "request_text": payload.text,
        "model_profile": request.app.state.settings.llm_model_profile,
        "messages": messages,
        "tools": TOOL_DEFINITIONS,
    }
    if payload.selection_context is not None:
        job_payload["selection_context"] = payload.selection_context.model_dump()
    row = await create_job(
        session,
        project_id=project_id,
        job_type="llm.complete",
        required_capabilities=["llm"],
        payload=job_payload,
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
    )
    return job_view(row)


@router.post("/api/v1/projects/{project_id}/jobs", status_code=201, dependencies=[Depends(require_csrf)])
async def job_create(
    project_id: str,
    payload: JobCreate,
    request: Request,
    session: DbSession,
    owner: OwnerSession,
):
    if await session.get(ProjectRow, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    payload_data = payload.payload
    if payload.job_type in {"image.generate", "image.edit"}:
        payload_data = ensure_generation_payload(payload_data, job_type=payload.job_type)
    row = await create_job(
        session,
        project_id=project_id,
        job_type=payload.job_type,
        payload=payload_data,
        required_capabilities=payload.required_capabilities,
        idempotency_key=payload.idempotency_key,
        correlation_id=request.state.request_id,
        dispatcher=request.app.state.job_dispatcher,
    )
    return job_view(row)


@router.get(
    "/api/v1/projects/{project_id}/jobs",
    dependencies=[Depends(require_owner)],
)
async def job_list(project_id: str, session: DbSession):
    result = await session.execute(
        select(JobRow)
        .where(JobRow.project_id == project_id)
        .order_by(JobRow.created_at.desc())
        .limit(100)
    )
    return [job_view(row) for row in result.scalars()]


@router.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_csrf)])
async def job_cancel(job_id: str, session: DbSession, owner: OwnerSession):
    row = await session.get(JobRow, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    row = await cancel_job(session, row)
    return job_view(row)


@router.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_owner)])
async def job_get(job_id: str, session: DbSession):
    row = await session.get(JobRow, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_view(row)


def job_view(row: JobRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "job_type": row.job_type,
        "status": row.status,
        "attempt": row.attempt,
        "idempotency_key": row.idempotency_key,
        "progress": row.progress,
        "correlation_id": row.correlation_id,
        "runtime_provenance": row.runtime_provenance,
        "result": row.result,
        "error": row.error,
        "leased_to": row.leased_to,
        "lease_expires_at": row.lease_expires_at,
        "created_at": row.created_at,
    }


@router.get("/api/v1/workers", dependencies=[Depends(require_owner)])
async def workers(request: Request, session: DbSession):
    result = await session.execute(select(WorkerRow).order_by(WorkerRow.id.asc()))
    rows = list(result.scalars())
    now = datetime.now(timezone.utc)
    grace = request.app.state.settings.worker_heartbeat_grace_seconds

    # Derive busy/current_job server-side: one query for all workers, picking
    # the deterministic first active job per worker (latest update, then id).
    active_jobs: dict[str, JobRow] = {}
    worker_ids = [row.id for row in rows]
    if worker_ids:
        leased = await session.execute(
            select(JobRow)
            .where(
                JobRow.leased_to.in_(worker_ids),
                JobRow.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(JobRow.updated_at.desc(), JobRow.id.asc())
        )
        for job in leased.scalars():
            if job.leased_to is not None and job.leased_to not in active_jobs:
                active_jobs[job.leased_to] = job

    def _as_utc(value: datetime | None) -> datetime | None:
        """Serialize timestamps with an explicit UTC offset.

        sqlite round-trips tz-aware datetimes as naive; an offset-less ISO
        string is parsed as *local* time by browser Date() constructors,
        which makes a fresh heartbeat read as stale away from UTC.
        """
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    return [
        {
            "id": row.id,
            "display_name": row.display_name,
            "online": (
                now
                - (
                    row.last_heartbeat
                    if row.last_heartbeat.tzinfo
                    else row.last_heartbeat.replace(tzinfo=timezone.utc)
                )
            ).total_seconds()
            <= grace,
            "capabilities": row.capabilities,
            "models": row.models,
            "runtimes": row.runtimes,
            "last_heartbeat": _as_utc(row.last_heartbeat),
            "hardware": row.hardware,
            "busy": row.id in active_jobs,
            "current_job": (
                {
                    "id": job.id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "project_id": job.project_id,
                }
                if (job := active_jobs.get(row.id)) is not None
                else None
            ),
        }
        for row in rows
    ]


@router.post("/api/v1/workers/register", dependencies=[Depends(require_worker)])
async def worker_register(payload: WorkerRegistration, session: DbSession):
    row = await session.get(WorkerRow, payload.worker_id)
    if row is None:
        row = WorkerRow(id=payload.worker_id)
        session.add(row)
    row.display_name = payload.display_name
    row.capabilities = payload.capabilities
    row.models = payload.models
    row.runtimes = payload.runtimes
    row.hardware = payload.hardware
    row.status = "online"
    row.last_heartbeat = datetime.now(timezone.utc)
    await session.commit()
    return {"worker_id": row.id, "status": row.status}


@router.post("/api/v1/workers/heartbeat", dependencies=[Depends(require_worker)])
async def worker_heartbeat(payload: WorkerHeartbeat, session: DbSession):
    row = await session.get(WorkerRow, payload.worker_id)
    if row is None:
        raise HTTPException(status_code=404, detail="worker not registered")
    row.status = "online"
    row.last_heartbeat = datetime.now(timezone.utc)
    await session.commit()
    return {"worker_id": row.id, "status": "online"}


@router.post("/api/v1/workers/jobs/claim", dependencies=[Depends(require_worker)])
async def worker_claim(payload: WorkerClaim, request: Request, session: DbSession):
    worker = await session.get(WorkerRow, payload.worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not registered")
    row = await claim_job(
        session,
        worker_id=worker.id,
        capabilities=set(worker.capabilities or []),
        lease_seconds=request.app.state.settings.worker_lease_seconds,
        models=set(worker.models or []),
        runtimes=worker.runtimes or {},
    )
    if row is None:
        return Response(status_code=204)
    downloads: dict[str, str] = {}
    for asset_id in row.payload.get("input_asset_ids", []):
        asset = await session.get(AssetRow, asset_id)
        if asset and asset.project_id == row.project_id:
            downloads[asset_id] = (
                f"/api/v1/workers/jobs/{row.id}/inputs/{asset_id}"
                f"?worker_id={worker.id}&lease_id={row.lease_id}"
            )
    return {
        "schema_version": "0.1.0",
        "job_id": row.id,
        "attempt": row.attempt,
        "lease_id": row.lease_id,
        "job_type": row.job_type,
        "expires_at": row.lease_expires_at,
        "required_capabilities": row.required_capabilities,
        "input_asset_ids": row.payload.get("input_asset_ids", []),
        "download_urls": downloads,
        "upload_targets": {},
        "payload": row.payload,
    }


async def _leased_job(session: AsyncSession, job_id: str) -> JobRow:
    row = await session.get(JobRow, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return row


@router.post("/api/v1/workers/jobs/{job_id}/start", dependencies=[Depends(require_worker)])
@router.post("/api/v1/workers/jobs/{job_id}/heartbeat", dependencies=[Depends(require_worker)])
async def worker_job_renew(job_id: str, payload: LeaseRequest, request: Request, session: DbSession):
    row = await _leased_job(session, job_id)
    try:
        row = await renew_lease(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
            lease_seconds=request.app.state.settings.worker_lease_seconds,
        )
    except ValueError as exc:
        raise _domain_conflict(exc) from exc

    worker = await session.get(WorkerRow, payload.worker_id)
    if worker is not None:
        worker.status = "online"
        worker.last_heartbeat = datetime.now(timezone.utc)
        await session.commit()
    return job_view(row)


@router.post(
    "/api/v1/workers/jobs/{job_id}/lease-status",
    dependencies=[Depends(require_worker)],
)
async def worker_job_lease_status(
    job_id: str,
    payload: LeaseRequest,
    session: DbSession,
):
    row = await _leased_job(session, job_id)
    if row.status == "cancelled":
        return {"status": "cancelled", "lease_valid": False}
    lease_valid = row.leased_to == payload.worker_id and row.lease_id == payload.lease_id
    return {"status": row.status, "lease_valid": lease_valid}


@router.post(
    "/api/v1/workers/jobs/{job_id}/release",
    dependencies=[Depends(require_worker)],
)
async def worker_job_release(
    job_id: str,
    payload: LeaseRequest,
    session: DbSession,
):
    row = await _leased_job(session, job_id)
    try:
        row = await release_job(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
        )
    except ValueError as exc:
        raise _domain_conflict(exc) from exc
    return job_view(row)


@router.post("/api/v1/workers/jobs/{job_id}/progress", dependencies=[Depends(require_worker)])
async def worker_job_progress(
    job_id: str,
    payload: JobProgress,
    request: Request,
    session: DbSession,
):
    row = await _leased_job(session, job_id)
    try:
        row = await update_progress(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
            progress=payload.progress,
            runtime_provenance=payload.runtime_provenance,
            lease_seconds=request.app.state.settings.worker_lease_seconds,
        )
    except ValueError as exc:
        raise _domain_conflict(exc) from exc
    return job_view(row)


@router.post("/api/v1/workers/jobs/{job_id}/complete", dependencies=[Depends(require_worker)])
async def worker_job_complete(
    job_id: str,
    payload: JobComplete,
    request: Request,
    session: DbSession,
):
    row = await _leased_job(session, job_id)
    try:
        processed_result = await apply_design_agent_result(
            session,
            row,
            payload.result,
            dispatcher=request.app.state.job_dispatcher,
        )
        if row.job_type == "style.analyze":
            style_row = await create_style_profile_from_job(
                session,
                row,
                processed_result,
            )
            processed_result = {
                **processed_result,
                "style_profile_id": style_row.id,
                "style_profile": style_row.profile_json,
            }
        if row.job_type == "plan.analyze":
            plan_row = await create_plan_draft_from_job(
                session,
                row,
                processed_result,
            )
            processed_result = {
                **processed_result,
                "draft_id": plan_row.id,
                "plan_draft": plan_row.draft_json,
            }
        if row.job_type == "render.blender":
            render_row = await persist_render_manifest(
                session,
                row,
                processed_result,
            )
            processed_result = {
                **processed_result,
                "render_id": render_row.id,
            }
        if (
            row.job_type in {"image.generate", "image.edit"}
            and isinstance(processed_result.get("generation_manifest"), dict)
        ):
            generation_row = await persist_generation_manifest(
                session,
                row,
                processed_result,
            )
            processed_result = {
                **processed_result,
                "generation_id": generation_row.id,
            }
        if row.job_type == "quality.geometry_check":
            diagnostic_row = await persist_geometry_diagnostic(
                session,
                row,
                processed_result,
            )
            processed_result = {
                **processed_result,
                "diagnostic_id": diagnostic_row.id,
            }
    except AgentToolError as exc:
        try:
            row = await fail_job(
                session,
                row,
                worker_id=payload.worker_id,
                lease_id=payload.lease_id,
                error=exc.as_error(),
                runtime_provenance=payload.runtime_provenance,
            )
        except ValueError as lease_exc:
            raise HTTPException(status_code=409, detail=str(lease_exc)) from lease_exc
        return job_view(row)
    except (ValueError, CommandRejected) as exc:
        try:
            row = await fail_job(
                session,
                row,
                worker_id=payload.worker_id,
                lease_id=payload.lease_id,
                error={
                    "code": "agent_result_rejected",
                    "detail": str(exc),
                    "context": {},
                },
                runtime_provenance=payload.runtime_provenance,
            )
        except ValueError as lease_exc:
            raise HTTPException(status_code=409, detail=str(lease_exc)) from lease_exc
        return job_view(row)

    try:
        row = await complete_job(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
            result=processed_result,
            runtime_provenance=payload.runtime_provenance,
        )
    except ValueError as exc:
        raise _domain_conflict(exc) from exc
    return job_view(row)


@router.get(
    "/api/v1/workers/jobs/{job_id}/inputs/{asset_id}",
    dependencies=[Depends(require_worker)],
)
async def worker_input_download(
    job_id: str,
    asset_id: str,
    request: Request,
    session: DbSession,
    worker_id: str,
    lease_id: str,
):
    job = await _leased_job(session, job_id)
    if job.leased_to != worker_id or job.lease_id != lease_id:
        raise HTTPException(status_code=409, detail="stale or invalid job lease")
    if asset_id not in (job.payload.get("input_asset_ids") or []):
        raise HTTPException(status_code=403, detail="asset is not an input of this job")
    asset = await session.get(AssetRow, asset_id)
    if asset is None or asset.project_id != job.project_id:
        raise HTTPException(status_code=404, detail="input asset not found")
    data = await request.app.state.object_store.get_bytes(asset.object_key)
    return Response(
        content=data,
        media_type=asset.media_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.post(
    "/api/v1/workers/jobs/{job_id}/outputs",
    status_code=201,
    dependencies=[Depends(require_worker)],
)
async def worker_output_upload(
    job_id: str,
    request: Request,
    session: DbSession,
    worker_id: str = Form(...),
    lease_id: str = Form(...),
    semantic_name: str | None = Form(None),
    file: UploadFile = File(...),
):
    job = await _leased_job(session, job_id)
    if job.leased_to != worker_id or job.lease_id != lease_id:
        raise HTTPException(status_code=409, detail="stale or invalid job lease")
    if not job.project_id:
        raise HTTPException(status_code=409, detail="job has no project")
    data, media_type = await _validated_upload(file, request)
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(file.filename or "").suffix[:16]
    object_key = f"projects/{job.project_id}/worker/{job.id}/{uuid4()}{suffix}"
    await request.app.state.object_store.put_bytes(object_key, data, media_type)
    asset = AssetRow(
        project_id=job.project_id,
        object_key=object_key,
        original_name=(file.filename or "")[:255] or None,
        media_type=media_type,
        size_bytes=len(data),
        sha256=digest,
        provenance="model_inferred",
        role="derived",
        metadata_json={
            **extract_asset_metadata(data, media_type),
            "job_id": job.id,
            **({"semantic_name": semantic_name} if semantic_name else {}),
        },
        source_asset_ids=list(job.payload.get("input_asset_ids") or []),
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return {
        "id": asset.id,
        "media_type": asset.media_type,
        "size_bytes": asset.size_bytes,
        "sha256": asset.sha256,
        "role": asset.role,
        "metadata": asset.metadata_json,
        "source_asset_ids": asset.source_asset_ids,
    }


@router.post("/api/v1/workers/jobs/{job_id}/fail", dependencies=[Depends(require_worker)])
async def worker_job_fail(job_id: str, payload: JobFail, session: DbSession):
    row = await _leased_job(session, job_id)
    try:
        row = await fail_job(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
            error=payload.error.model_dump(mode="json"),
            runtime_provenance=payload.runtime_provenance,
        )
    except ValueError as exc:
        raise _domain_conflict(exc) from exc
    return job_view(row)
