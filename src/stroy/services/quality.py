from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import GeometryDiagnosticRow, JobRow
from stroy.quality import GeometryDiagnostic


async def persist_geometry_diagnostic(
    session: AsyncSession,
    job: JobRow,
    result: dict,
) -> GeometryDiagnosticRow:
    if job.job_type != "quality.geometry_check":
        raise ValueError("job is not a geometry quality job")
    if not job.project_id:
        raise ValueError("quality job has no project")

    raw = result.get("geometry_diagnostic")
    if not isinstance(raw, dict):
        raise ValueError("quality result requires geometry_diagnostic")
    diagnostic = GeometryDiagnostic.model_validate(raw)

    payload = job.payload
    if diagnostic.scene_revision_id != payload.get("scene_revision_id"):
        raise ValueError("diagnostic scene revision does not match job")
    if diagnostic.camera_id != payload.get("camera_id"):
        raise ValueError("diagnostic camera does not match job")
    if diagnostic.reference_asset_id != payload.get("reference_asset_id"):
        raise ValueError("diagnostic reference asset does not match job")
    if diagnostic.generated_asset_id != payload.get("generated_asset_id"):
        raise ValueError("diagnostic generated asset does not match job")

    existing = (
        await session.execute(
            select(GeometryDiagnosticRow).where(GeometryDiagnosticRow.job_id == job.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = GeometryDiagnosticRow(
        id=diagnostic.diagnostic_id,
        project_id=job.project_id,
        job_id=job.id,
        scene_revision_id=diagnostic.scene_revision_id,
        camera_id=diagnostic.camera_id,
        reference_asset_id=diagnostic.reference_asset_id,
        generated_asset_id=diagnostic.generated_asset_id,
        diagnostic_json=diagnostic.model_dump(mode="json"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_geometry_diagnostics(
    session: AsyncSession,
    project_id: str,
) -> list[GeometryDiagnosticRow]:
    result = await session.execute(
        select(GeometryDiagnosticRow)
        .where(GeometryDiagnosticRow.project_id == project_id)
        .order_by(GeometryDiagnosticRow.created_at.desc(), GeometryDiagnosticRow.id.desc())
    )
    return list(result.scalars())
