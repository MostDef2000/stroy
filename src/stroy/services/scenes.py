from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import DesignCommandRow, ProjectRow, SceneRevisionRow
from stroy.domain.commands import CommandConflict, apply_command
from stroy.domain.models import DesignCommand, Scene, canonical_hash


async def create_project(session: AsyncSession, name: str) -> ProjectRow:
    row = ProjectRow(name=name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def initialize_scene(
    session: AsyncSession, project_id: str, scene: Scene
) -> SceneRevisionRow:
    if scene.project_id != project_id:
        raise ValueError("scene.project_id must match route project_id")
    if await latest_revision(session, project_id):
        raise CommandConflict("project scene already initialized")
    row = SceneRevisionRow(
        project_id=project_id,
        parent_revision_id=None,
        command_id=None,
        content_hash=canonical_hash(scene),
        scene_json=scene.model_dump(mode="json", exclude_none=True),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def latest_revision(session: AsyncSession, project_id: str) -> SceneRevisionRow | None:
    result = await session.execute(
        select(SceneRevisionRow)
        .where(SceneRevisionRow.project_id == project_id)
        .order_by(SceneRevisionRow.created_at.desc(), SceneRevisionRow.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def apply_scene_command(
    session: AsyncSession, project_id: str, command: DesignCommand
) -> SceneRevisionRow:
    current = await latest_revision(session, project_id)
    if current is None:
        raise CommandConflict("scene is not initialized")
    if current.id != command.base_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {current.id}, got {command.base_revision_id}"
        )

    scene = Scene.model_validate(current.scene_json)
    next_scene = apply_command(scene, command)

    session.add(
        DesignCommandRow(
            id=command.command_id,
            project_id=project_id,
            base_revision_id=command.base_revision_id,
            operation=command.operation.value,
            target_id=command.target_id,
            parameters=command.parameters,
            reference_asset_ids=command.reference_asset_ids,
            origin=command.origin.value,
            request_text=command.request_text,
        )
    )
    revision = SceneRevisionRow(
        project_id=project_id,
        parent_revision_id=current.id,
        command_id=command.command_id,
        content_hash=canonical_hash(next_scene),
        scene_json=next_scene.model_dump(mode="json", exclude_none=True),
    )
    session.add(revision)
    await session.commit()
    await session.refresh(revision)
    return revision


async def list_revisions(
    session: AsyncSession, project_id: str
) -> list[SceneRevisionRow]:
    result = await session.execute(
        select(SceneRevisionRow)
        .where(SceneRevisionRow.project_id == project_id)
        .order_by(SceneRevisionRow.created_at.desc(), SceneRevisionRow.id.desc())
    )
    return list(result.scalars())


async def revert_scene(
    session: AsyncSession,
    project_id: str,
    *,
    expected_base_revision_id: str,
    target_revision_id: str,
) -> SceneRevisionRow:
    current = await latest_revision(session, project_id)
    if current is None:
        raise CommandConflict("scene is not initialized")
    if current.id != expected_base_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {current.id}, got {expected_base_revision_id}"
        )
    target = await session.get(SceneRevisionRow, target_revision_id)
    if target is None or target.project_id != project_id:
        raise CommandConflict("target revision not found in project")

    scene = Scene.model_validate(target.scene_json)
    revision = SceneRevisionRow(
        project_id=project_id,
        parent_revision_id=current.id,
        command_id=None,
        content_hash=canonical_hash(scene),
        scene_json=scene.model_dump(mode="json", exclude_none=True),
    )
    session.add(revision)
    await session.commit()
    await session.refresh(revision)
    return revision
