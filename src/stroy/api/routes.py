from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.agent import TOOL_DEFINITIONS
from stroy.api.dependencies import (
    DbSession,
    OwnerSession,
    require_csrf,
    require_owner,
    require_worker,
)
from stroy.db.models import AssetRow, AuthSessionRow, JobRow, ProjectRow, WorkerRow
from stroy.domain.commands import CommandConflict, CommandRejected
from stroy.domain.models import DesignCommand, Scene
from stroy.security import random_token, sha256_text, verify_password
from stroy.services.agent import apply_design_agent_result
from stroy.services.asset_metadata import extract_asset_metadata
from stroy.services.jobs import (
    cancel_job,
    claim_job,
    complete_job,
    create_job,
    fail_job,
    renew_lease,
    update_progress,
)
from stroy.services.scenes import (
    apply_scene_command,
    create_project,
    initialize_scene,
    latest_revision,
    list_revisions,
    revert_scene,
)


router = APIRouter()


def _domain_conflict(exc: ValueError) -> HTTPException:
    code = "revision_conflict" if isinstance(exc, CommandConflict) else "command_rejected"
    return HTTPException(
        status_code=409,
        detail={"code": code, "detail": str(exc)},
    )


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


class DesignInstruction(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    idempotency_key: str | None = Field(default=None, max_length=160)


class SceneRevert(BaseModel):
    expected_base_revision_id: str
    target_revision_id: str


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
    if role not in {"apartment", "reference", "derived"}:
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
    row = await create_job(
        session,
        project_id=project_id,
        job_type="llm.complete",
        required_capabilities=["llm"],
        payload={
            "purpose": "design_instruction",
            "base_revision_id": current.id,
            "request_text": payload.text,
            "model_profile": request.app.state.settings.llm_model_profile,
            "messages": [{"role": "user", "content": payload.text}],
            "tools": TOOL_DEFINITIONS,
        },
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
    row = await create_job(
        session,
        project_id=project_id,
        job_type=payload.job_type,
        payload=payload.payload,
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
    now = datetime.now(timezone.utc)
    grace = request.app.state.settings.worker_heartbeat_grace_seconds
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
            "last_heartbeat": row.last_heartbeat,
        }
        for row in result.scalars()
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
    except (ValueError, CommandRejected) as exc:
        try:
            row = await fail_job(
                session,
                row,
                worker_id=payload.worker_id,
                lease_id=payload.lease_id,
                error={"code": "agent_result_rejected", "detail": str(exc)},
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
