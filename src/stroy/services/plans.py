"""Plan draft persistence and draft -> canonical scene construction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import JobRow, PlanDraftRow, SceneRevisionRow
from stroy.domain.models import (
    EntityState,
    EntityKind,
    Provenance,
    ProvenanceSource,
    Scene,
    SceneEntity,
    Transform,
)
from stroy.domain.plan import PlanDraft, PlanFloor, PlanRoom, PlanScaleSource, PlanWall
from stroy.services.scenes import append_snapshot_revision, latest_revision

# Bare-apartment MVP-2: single storey height for every wall.
# TODO: derive per-floor wall height from plan annotations / owner input.
SCENE_WALL_HEIGHT_MM = 2700.0


class ScaleUnknownError(ValueError):
    """Commit was requested before the owner supplied real dimensions."""


class DraftAlreadyCommittedError(ValueError):
    """The latest draft has already been committed to a scene revision."""


async def _next_version(session: AsyncSession, project_id: str) -> int:
    result = await session.execute(
        select(func.max(PlanDraftRow.version)).where(
            PlanDraftRow.project_id == project_id
        )
    )
    current = result.scalar_one_or_none()
    return int(current or 0) + 1


async def save_draft(
    session: AsyncSession,
    project_id: str,
    draft: PlanDraft,
    *,
    job_id: str | None = None,
    status: str = "draft",
) -> PlanDraftRow:
    """Persist a new immutable draft version for the project."""
    row = PlanDraftRow(
        project_id=project_id,
        version=await _next_version(session, project_id),
        draft_json=draft.model_dump(mode="json", exclude_none=True),
        status=status,
        job_id=job_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_latest_draft(
    session: AsyncSession, project_id: str
) -> PlanDraftRow | None:
    result = await session.execute(
        select(PlanDraftRow)
        .where(PlanDraftRow.project_id == project_id)
        .order_by(PlanDraftRow.version.desc(), PlanDraftRow.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_drafts(
    session: AsyncSession, project_id: str
) -> list[PlanDraftRow]:
    result = await session.execute(
        select(PlanDraftRow)
        .where(PlanDraftRow.project_id == project_id)
        .order_by(PlanDraftRow.version.desc(), PlanDraftRow.created_at.desc())
    )
    return list(result.scalars())


async def create_plan_draft_from_job(
    session: AsyncSession,
    job: JobRow,
    result: dict[str, Any],
) -> PlanDraftRow:
    if job.job_type != "plan.analyze":
        raise ValueError("job is not a plan analysis job")
    if not job.project_id:
        raise ValueError("plan analysis job has no project")
    raw = result.get("plan_draft")
    if not isinstance(raw, dict):
        raise ValueError("plan analysis result requires plan_draft object")
    draft = PlanDraft.model_validate(raw)
    return await save_draft(session, job.project_id, draft, job_id=job.id)


def _room_centroid(room: PlanRoom, walls_by_id: dict[str, PlanWall]) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for wall_id in room.wall_ids:
        wall = walls_by_id.get(wall_id)
        if wall is None:
            continue
        xs.append((wall.x1 + wall.x2) / 2.0)
        ys.append((wall.y1 + wall.y2) / 2.0)
    if not xs:
        return (0.0, 0.0)
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _room_bbox(
    room: PlanRoom, walls_by_id: dict[str, PlanWall]
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for wall_id in room.wall_ids:
        wall = walls_by_id.get(wall_id)
        if wall is None:
            continue
        xs.extend([wall.x1, wall.x2])
        ys.extend([wall.y1, wall.y2])
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


_OPENING_KINDS = {
    "door": EntityKind.DOOR,
    "window": EntityKind.WINDOW,
    "arch": EntityKind.ARCHITECTURAL,
}


def _build_floor_entities(
    floor: PlanFloor,
    *,
    wall_height_mm: float,
    used_ids: set[str],
    note: str,
) -> list[SceneEntity]:
    entities: list[SceneEntity] = []
    walls_by_id = {wall.id: wall for wall in floor.walls}

    # A wall belongs to the first room that lists it; shared walls keep the
    # first owner only (phase A, single-floor MVP).
    wall_room: dict[str, str] = {}
    for room in floor.rooms:
        for wall_id in room.wall_ids:
            wall_room.setdefault(wall_id, room.id)

    def add(entity: SceneEntity) -> None:
        if entity.id in used_ids:
            raise ValueError(f"duplicate scene entity id from plan draft: {entity.id}")
        used_ids.add(entity.id)
        entities.append(entity)

    for wall in floor.walls:
        length = wall.length_mm
        add(
            SceneEntity(
                id=wall.id,
                kind=EntityKind.WALL,
                state=EntityState.STRUCTURE,
                room_id=wall_room.get(wall.id),
                geometry={
                    "a": [wall.x1, wall.y1],
                    "b": [wall.x2, wall.y2],
                    "dimensions_mm": {
                        "length": length,
                        "thickness": wall.thickness_mm,
                        "height": wall_height_mm,
                    },
                },
                provenance=Provenance(
                    source=ProvenanceSource.MODEL_INFERRED, note=note
                ),
            )
        )
        for opening in wall.openings:
            add(
                SceneEntity(
                    id=opening.id,
                    kind=_OPENING_KINDS[opening.kind],
                    state=EntityState.STRUCTURE,
                    room_id=wall_room.get(wall.id),
                    geometry={
                        "host_wall_id": wall.id,
                        "t": opening.t,
                        "width_mm": opening.width_mm,
                        "height_mm": opening.height_mm,
                        "sill_mm": opening.sill_mm,
                    },
                    metadata={"opening_kind": opening.kind},
                    provenance=Provenance(
                        source=ProvenanceSource.MODEL_INFERRED, note=note
                    ),
                )
            )

    for room in floor.rooms:
        centroid = _room_centroid(room, walls_by_id)
        add(
            SceneEntity(
                id=room.id,
                kind=EntityKind.ROOM,
                state=EntityState.STRUCTURE,
                display_name=room.name,
                geometry={
                    "wall_ids": list(room.wall_ids),
                    "centroid": [centroid[0], centroid[1]],
                },
                metadata={"floor_finish": room.floor_finish},
                provenance=Provenance(
                    source=ProvenanceSource.MODEL_INFERRED, note=note
                ),
            )
        )

        min_x, min_y, max_x, max_y = _room_bbox(room, walls_by_id)
        width = max_x - min_x
        depth = max_y - min_y
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        add(
            SceneEntity(
                id=f"floor.{room.id}",
                kind=EntityKind.FLOOR,
                state=EntityState.STRUCTURE,
                room_id=room.id,
                transform=Transform(
                    translation_mm=(center_x, center_y, floor.level_mm)
                ),
                geometry={
                    "dimensions_mm": [width, depth, 0.0],
                    "bbox": [min_x, min_y, max_x, max_y],
                },
                provenance=Provenance(
                    source=ProvenanceSource.MODEL_INFERRED, note=note
                ),
            )
        )
        add(
            SceneEntity(
                id=f"ceiling.{room.id}",
                kind=EntityKind.CEILING,
                state=EntityState.STRUCTURE,
                room_id=room.id,
                transform=Transform(
                    translation_mm=(
                        center_x,
                        center_y,
                        floor.level_mm + wall_height_mm,
                    )
                ),
                geometry={
                    "dimensions_mm": [width, depth, 0.0],
                    "bbox": [min_x, min_y, max_x, max_y],
                },
                provenance=Provenance(
                    source=ProvenanceSource.MODEL_INFERRED, note=note
                ),
            )
        )

    return entities


def build_scene_from_draft(
    project_id: str,
    draft: PlanDraft,
    *,
    wall_height_mm: float = SCENE_WALL_HEIGHT_MM,
    source_revision: int | None = None,
) -> Scene:
    """Map a corrected draft to a canonical scene (deterministic ids)."""
    if draft.scale.source is PlanScaleSource.UNKNOWN:
        raise ScaleUnknownError(
            "plan scale is unknown; set scale before committing a scene"
        )

    note = "reconstructed from plan draft"
    if source_revision is not None:
        note = f"{note} v{source_revision}"

    entities: list[SceneEntity] = []
    used_ids: set[str] = set()
    for floor in draft.floors:
        entities.extend(
            _build_floor_entities(
                floor,
                wall_height_mm=wall_height_mm,
                used_ids=used_ids,
                note=note,
            )
        )

    return Scene(
        scene_id=f"scene.plan.{project_id}",
        project_id=project_id,
        entities=entities,
        cameras=[],
    )


async def commit_draft(
    session: AsyncSession,
    project_id: str,
    draft_row: PlanDraftRow,
) -> SceneRevisionRow:
    """Build the canonical scene from the latest draft and snapshot it."""
    if draft_row.status == "committed":
        raise DraftAlreadyCommittedError("latest plan draft is already committed")
    draft = PlanDraft.model_validate(draft_row.draft_json)
    scene = build_scene_from_draft(
        project_id,
        draft,
        source_revision=draft_row.version,
    )
    current = await latest_revision(session, project_id)
    revision = await append_snapshot_revision(
        session,
        project_id,
        scene,
        parent_revision_id=current.id if current else None,
    )
    draft_row.status = "committed"
    await session.commit()
    await session.refresh(draft_row)
    return revision
