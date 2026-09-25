from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import JobRow
from stroy.services.dispatch import JobDispatcher


logger = logging.getLogger("stroy.jobs")

ACTIVE_JOB_STATUSES = {"leased", "running"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _log(event: str, row: JobRow, **extra) -> None:
    logger.info(
        json.dumps(
            {
                "event": event,
                "job_id": row.id,
                "project_id": row.project_id,
                "job_type": row.job_type,
                "revision_id": (
                    (row.payload or {}).get("scene_revision_id")
                    or (row.payload or {}).get("base_revision_id")
                ),
                "status": row.status,
                "attempt": row.attempt,
                "correlation_id": row.correlation_id,
                **extra,
            },
            separators=(",", ":"),
        )
    )


async def _find_idempotent(
    session: AsyncSession,
    *,
    project_id: str | None,
    job_type: str,
    idempotency_key: str,
) -> JobRow | None:
    result = await session.execute(
        select(JobRow).where(
            JobRow.project_id == project_id,
            JobRow.job_type == job_type,
            JobRow.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def create_job(
    session: AsyncSession,
    *,
    job_type: str,
    payload: dict,
    project_id: str | None = None,
    required_capabilities: list[str] | None = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    runtime_provenance: dict | None = None,
    dispatcher: JobDispatcher | None = None,
) -> JobRow:
    if idempotency_key:
        existing = await _find_idempotent(
            session,
            project_id=project_id,
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing

    row = JobRow(
        project_id=project_id,
        job_type=job_type,
        payload=payload,
        required_capabilities=required_capabilities or [],
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        runtime_provenance=runtime_provenance or {},
        progress={},
        status="waiting_for_worker" if required_capabilities else "queued",
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if not idempotency_key:
            raise
        existing = await _find_idempotent(
            session,
            project_id=project_id,
            job_type=job_type,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            raise
        return existing

    await session.refresh(row)
    _log("job_created", row)

    if dispatcher is not None:
        try:
            await dispatcher.notify(row.id)
        except Exception:
            logger.exception(
                "job dispatch notification failed",
                extra={"job_id": row.id, "project_id": row.project_id},
            )
    return row


async def _requeue_expired(session: AsyncSession) -> None:
    now = utcnow()
    result = await session.execute(
        select(JobRow).where(
            JobRow.status.in_(ACTIVE_JOB_STATUSES),
            JobRow.lease_expires_at.is_not(None),
            JobRow.lease_expires_at < now,
        )
    )
    changed = False
    for row in result.scalars():
        row.status = "waiting_for_worker" if row.required_capabilities else "queued"
        row.leased_to = None
        row.lease_id = None
        row.lease_expires_at = None
        row.progress = {"phase": "requeued_after_lease_expiry"}
        _log("job_lease_expired", row)
        changed = True
    if changed:
        await session.commit()


async def claim_job(
    session: AsyncSession,
    *,
    worker_id: str,
    capabilities: set[str],
    lease_seconds: int,
) -> JobRow | None:
    await _requeue_expired(session)
    result = await session.execute(
        select(JobRow)
        .where(JobRow.status.in_(["queued", "waiting_for_worker"]))
        .order_by(JobRow.created_at.asc(), JobRow.id.asc())
        .limit(100)
    )
    for row in result.scalars():
        required = set(row.required_capabilities or [])
        if not required.issubset(capabilities):
            if row.status == "queued":
                row.status = "waiting_for_worker"
            continue
        row.status = "leased"
        row.leased_to = worker_id
        row.lease_id = str(uuid4())
        row.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
        row.progress = {"phase": "leased"}
        row.attempt += 1
        await session.commit()
        await session.refresh(row)
        _log("job_claimed", row, worker_id=worker_id)
        return row
    await session.commit()
    return None


def _require_lease(row: JobRow, worker_id: str, lease_id: str) -> None:
    if row.status == "cancelled":
        raise ValueError("job was cancelled")
    if row.leased_to != worker_id or row.lease_id != lease_id:
        raise ValueError("stale or invalid job lease")
    if row.lease_expires_at:
        expires_at = row.lease_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utcnow():
            raise ValueError("job lease expired")


async def renew_lease(
    session: AsyncSession,
    row: JobRow,
    *,
    worker_id: str,
    lease_id: str,
    lease_seconds: int,
    running: bool = True,
) -> JobRow:
    _require_lease(row, worker_id, lease_id)
    row.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    if running:
        row.status = "running"
        if not row.progress:
            row.progress = {"phase": "running"}
    await session.commit()
    await session.refresh(row)
    return row


async def update_progress(
    session: AsyncSession,
    row: JobRow,
    *,
    worker_id: str,
    lease_id: str,
    progress: dict,
    lease_seconds: int,
    runtime_provenance: dict | None = None,
) -> JobRow:
    _require_lease(row, worker_id, lease_id)
    row.status = "running"
    row.progress = progress
    row.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    if runtime_provenance:
        row.runtime_provenance = {
            **(row.runtime_provenance or {}),
            **runtime_provenance,
        }
    await session.commit()
    await session.refresh(row)
    return row


async def cancel_job(session: AsyncSession, row: JobRow) -> JobRow:
    if row.status in TERMINAL_JOB_STATUSES:
        return row
    row.status = "cancelled"
    row.progress = {"phase": "cancelled"}
    row.leased_to = None
    row.lease_id = None
    row.lease_expires_at = None
    await session.commit()
    await session.refresh(row)
    _log("job_cancelled", row)
    return row


async def complete_job(
    session: AsyncSession,
    row: JobRow,
    *,
    worker_id: str,
    lease_id: str,
    result: dict,
    runtime_provenance: dict | None = None,
) -> JobRow:
    if row.status == "succeeded" and row.lease_id == lease_id:
        return row
    _require_lease(row, worker_id, lease_id)
    row.status = "succeeded"
    row.result = result
    row.error = None
    row.progress = {"phase": "succeeded", "fraction": 1.0}
    row.lease_expires_at = None
    if runtime_provenance:
        row.runtime_provenance = {
            **(row.runtime_provenance or {}),
            **runtime_provenance,
        }
    await session.commit()
    await session.refresh(row)
    _log("job_succeeded", row, worker_id=worker_id)
    return row


async def fail_job(
    session: AsyncSession,
    row: JobRow,
    *,
    worker_id: str,
    lease_id: str,
    error: dict,
    runtime_provenance: dict | None = None,
) -> JobRow:
    if row.status == "failed" and row.lease_id == lease_id:
        return row
    _require_lease(row, worker_id, lease_id)
    row.status = "failed"
    row.error = error
    row.progress = {"phase": "failed"}
    row.lease_expires_at = None
    if runtime_provenance:
        row.runtime_provenance = {
            **(row.runtime_provenance or {}),
            **runtime_provenance,
        }
    await session.commit()
    await session.refresh(row)
    _log("job_failed", row, worker_id=worker_id, error_code=error.get("code"))
    return row
