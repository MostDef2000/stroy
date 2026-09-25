from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import JobRow


ACTIVE_JOB_STATUSES = {"leased", "running"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_job(
    session: AsyncSession,
    *,
    job_type: str,
    payload: dict,
    project_id: str | None = None,
    required_capabilities: list[str] | None = None,
) -> JobRow:
    row = JobRow(
        project_id=project_id,
        job_type=job_type,
        payload=payload,
        required_capabilities=required_capabilities or [],
        status="queued",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
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
        row.status = "queued"
        row.leased_to = None
        row.lease_id = None
        row.lease_expires_at = None
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
        row.attempt += 1
        await session.commit()
        await session.refresh(row)
        return row
    await session.commit()
    return None


def _require_lease(row: JobRow, worker_id: str, lease_id: str) -> None:
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
    await session.commit()
    await session.refresh(row)
    return row


async def complete_job(
    session: AsyncSession,
    row: JobRow,
    *,
    worker_id: str,
    lease_id: str,
    result: dict,
) -> JobRow:
    if row.status == "succeeded" and row.lease_id == lease_id:
        return row
    _require_lease(row, worker_id, lease_id)
    row.status = "succeeded"
    row.result = result
    row.error = None
    row.lease_expires_at = None
    await session.commit()
    await session.refresh(row)
    return row


async def fail_job(
    session: AsyncSession,
    row: JobRow,
    *,
    worker_id: str,
    lease_id: str,
    error: dict,
) -> JobRow:
    if row.status == "failed" and row.lease_id == lease_id:
        return row
    _require_lease(row, worker_id, lease_id)
    row.status = "failed"
    row.error = error
    row.lease_expires_at = None
    await session.commit()
    await session.refresh(row)
    return row
