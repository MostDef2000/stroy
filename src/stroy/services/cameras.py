from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.camera import camera_residual_px
from stroy.db.models import AssetRow, SceneRevisionRow
from stroy.domain.commands import CommandConflict
from stroy.domain.models import Camera, Scene, canonical_hash
from stroy.services.scenes import latest_revision


async def upsert_camera(
    session: AsyncSession,
    project_id: str,
    *,
    expected_base_revision_id: str,
    camera: Camera,
) -> SceneRevisionRow:
    current = await latest_revision(session, project_id)
    if current is None:
        raise CommandConflict("scene is not initialized")
    if current.id != expected_base_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {current.id}, got {expected_base_revision_id}"
        )

    if camera.source_asset_id:
        asset = await session.get(AssetRow, camera.source_asset_id)
        if asset is None or asset.project_id != project_id:
            raise ValueError("camera source asset is not in project")
        if not asset.media_type.startswith("image/"):
            raise ValueError("camera source asset must be an image")

    next_camera = camera.model_copy(deep=True)
    if next_camera.calibration and next_camera.calibration.observations:
        residual = camera_residual_px(next_camera)
        if residual is None:
            raise ValueError("camera calibration observations are not projectable")
        next_camera.calibration.residual = residual
        if next_camera.calibration.quality is None:
            next_camera.calibration.quality = max(0.0, min(1.0, 1.0 - residual / 20.0))
        next_camera.calibration.method = "correspondences"

    scene = Scene.model_validate(current.scene_json)
    index = next(
        (index for index, item in enumerate(scene.cameras) if item.id == next_camera.id),
        None,
    )
    if index is None:
        scene.cameras.append(next_camera)
    else:
        scene.cameras[index] = next_camera

    revision = SceneRevisionRow(
        project_id=project_id,
        parent_revision_id=current.id,
        command_id=None,
        content_hash=canonical_hash(scene),
        scene_json=scene.model_dump(mode="json", exclude_none=True),
    )
    session.add(revision)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CommandConflict("base revision was updated concurrently") from exc
    await session.refresh(revision)
    return revision


async def remove_camera(
    session: AsyncSession,
    project_id: str,
    *,
    expected_base_revision_id: str,
    camera_id: str,
) -> SceneRevisionRow:
    current = await latest_revision(session, project_id)
    if current is None:
        raise CommandConflict("scene is not initialized")
    if current.id != expected_base_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {current.id}, got {expected_base_revision_id}"
        )

    scene = Scene.model_validate(current.scene_json)
    original = len(scene.cameras)
    scene.cameras = [camera for camera in scene.cameras if camera.id != camera_id]
    if len(scene.cameras) == original:
        raise ValueError(f"unknown camera: {camera_id}")

    revision = SceneRevisionRow(
        project_id=project_id,
        parent_revision_id=current.id,
        command_id=None,
        content_hash=canonical_hash(scene),
        scene_json=scene.model_dump(mode="json", exclude_none=True),
    )
    session.add(revision)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CommandConflict("base revision was updated concurrently") from exc
    await session.refresh(revision)
    return revision
