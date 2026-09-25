from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.api.dependencies import DbSession, OwnerSession, require_csrf, require_owner, require_worker
from stroy.db.models import AssetRow, AuthSessionRow, JobRow, ProjectRow, WorkerRow
from stroy.domain.commands import CommandRejected
from stroy.domain.models import DesignCommand, Scene
from stroy.security import random_token, sha256_text, verify_password
from stroy.services.jobs import claim_job, complete_job, create_job, fail_job, renew_lease
from stroy.services.scenes import apply_scene_command, create_project, initialize_scene, latest_revision


router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class JobCreate(BaseModel):
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)


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


class JobComplete(LeaseRequest):
    result: dict[str, Any] = Field(default_factory=dict)


class JobFail(LeaseRequest):
    error: dict[str, Any]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: DbSession) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ready"}


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
    return {"authenticated": True, "username": request.app.state.settings.auth_username}


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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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


@router.post("/api/v1/projects/{project_id}/scene/commands", dependencies=[Depends(require_csrf)])
async def scene_command(project_id: str, command: DesignCommand, session: DbSession, owner: OwnerSession):
    try:
        revision = await apply_scene_command(session, project_id, command)
    except (ValueError, CommandRejected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    file: UploadFile = File(...),
):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(file.filename or "").suffix[:16]
    object_key = f"projects/{project_id}/{uuid4()}{suffix}"
    media_type = file.content_type or "application/octet-stream"
    await request.app.state.object_store.put_bytes(object_key, data, media_type)
    row = AssetRow(
        project_id=project_id,
        object_key=object_key,
        original_name=(file.filename or "")[:255] or None,
        media_type=media_type,
        size_bytes=len(data),
        sha256=digest,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "media_type": row.media_type, "size_bytes": row.size_bytes, "sha256": row.sha256}


@router.get("/api/v1/assets/{asset_id}", dependencies=[Depends(require_owner)])
async def asset_download(asset_id: str, request: Request, session: DbSession):
    row = await session.get(AssetRow, asset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="asset not found")
    url = await request.app.state.object_store.presign_get(row.object_key)
    if url:
        return RedirectResponse(url)
    data = await request.app.state.object_store.get_bytes(row.object_key)
    return Response(content=data, media_type=row.media_type)


@router.post("/api/v1/projects/{project_id}/jobs", status_code=201, dependencies=[Depends(require_csrf)])
async def job_create(project_id: str, payload: JobCreate, session: DbSession, owner: OwnerSession):
    row = await create_job(
        session,
        project_id=project_id,
        job_type=payload.job_type,
        payload=payload.payload,
        required_capabilities=payload.required_capabilities,
    )
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
        "result": row.result,
        "error": row.error,
        "leased_to": row.leased_to,
        "lease_expires_at": row.lease_expires_at,
        "created_at": row.created_at,
    }


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
        if asset:
            url = await request.app.state.object_store.presign_get(asset.object_key)
            if url:
                downloads[asset_id] = url
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_view(row)


@router.post("/api/v1/workers/jobs/{job_id}/complete", dependencies=[Depends(require_worker)])
async def worker_job_complete(job_id: str, payload: JobComplete, session: DbSession):
    row = await _leased_job(session, job_id)
    try:
        row = await complete_job(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
            result=payload.result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_view(row)


@router.post("/api/v1/workers/jobs/{job_id}/fail", dependencies=[Depends(require_worker)])
async def worker_job_fail(job_id: str, payload: JobFail, session: DbSession):
    row = await _leased_job(session, job_id)
    try:
        row = await fail_job(
            session,
            row,
            worker_id=payload.worker_id,
            lease_id=payload.lease_id,
            error=payload.error,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_view(row)
