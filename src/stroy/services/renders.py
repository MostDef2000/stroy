from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import JobRow, RenderManifestRow
from stroy.rendering import RenderManifest


async def persist_render_manifest(
    session: AsyncSession,
    job: JobRow,
    result: dict,
) -> RenderManifestRow:
    if job.job_type != "render.blender":
        raise ValueError("job is not a Blender render job")
    if not job.project_id:
        raise ValueError("render job has no project")

    raw = result.get("render_manifest")
    if not isinstance(raw, dict):
        raise ValueError("render result requires render_manifest")
    manifest = RenderManifest.model_validate(raw)

    if manifest.scene_revision_id != job.payload.get("scene_revision_id"):
        raise ValueError("render manifest scene revision does not match job")
    if manifest.camera_id != job.payload.get("camera_id"):
        raise ValueError("render manifest camera does not match job")

    existing = (
        await session.execute(
            select(RenderManifestRow).where(RenderManifestRow.job_id == job.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = RenderManifestRow(
        id=manifest.render_id,
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


async def list_render_manifests(
    session: AsyncSession,
    project_id: str,
) -> list[RenderManifestRow]:
    result = await session.execute(
        select(RenderManifestRow)
        .where(RenderManifestRow.project_id == project_id)
        .order_by(RenderManifestRow.created_at.desc(), RenderManifestRow.id.desc())
    )
    return list(result.scalars())
