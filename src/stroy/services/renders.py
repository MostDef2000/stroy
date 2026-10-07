from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import JobRow, RenderManifestRow
from stroy.rendering import RenderManifest


def _render_seconds_from_scene_metadata(scene_metadata: object) -> float | None:
    """Guarded numeric cast of the Blender-measured render time.

    scene_metadata is untrusted worker output; accept only real int/float
    (bool is an int subclass and must not count) and ignore anything else.
    """
    if not isinstance(scene_metadata, dict):
        return None
    value = scene_metadata.get("render_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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
    # The Blender renderer measures wall-clock render_seconds into the worker's
    # scene_metadata; lift it onto the typed manifest so the persisted JSON is
    # schema-valid and the renders API can expose it. Absent/foreign values
    # leave the optional field unset (old manifests keep serving with None).
    if manifest.render_seconds is None:
        manifest.render_seconds = _render_seconds_from_scene_metadata(
            result.get("scene_metadata")
        )

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

    manifest_json = manifest.model_dump(mode="json", exclude_none=True)

    row = RenderManifestRow(
        id=manifest.render_id,
        project_id=job.project_id,
        job_id=job.id,
        scene_revision_id=manifest.scene_revision_id,
        design_revision_id=manifest.design_revision_id,
        camera_id=manifest.camera_id,
        # R4: variant linkage carried from the job payload (NULL legacy).
        variant_id=job.payload.get("variant_id"),
        manifest_json=manifest_json,
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
