"""Scene variant service (R4): isolated design branches over immutable revisions.

A variant pins ``base_scene_revision_id`` (fork point) and
``head_scene_revision_id`` (current tip) into ``scene_revisions``, which stays
immutable and shared with the canonical scene. Lineage = the head plus the
parent-chain walk over revisions.

Invariants enforced here (migration 0015 dropped the DB-level UNIQUE parent
lock, so linear-history protection is service-side):

- the ONLY reader of the global latest revision is
  :func:`create_variant_from_current` (seeding);
- at most one ``approved`` variant per project — approving demotes the
  previous approved to ``shortlisted`` in the same transaction;
- variant-scoped appends are guarded by an expected-head optimistic check
  (``CommandConflict`` on stale head, replacing the old unique lock);
- variant deletion removes the row only — revisions are never touched.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import (
    DesignCommandRow,
    ProjectRow,
    RenderManifestRow,
    SceneRevisionRow,
    SceneVariantRow,
    utcnow,
)
from stroy.domain.commands import CommandConflict, apply_command
from stroy.domain.models import DesignCommand, Scene, canonical_hash
from stroy.services.budget import budget_report
from stroy.services.scenes import latest_revision
from stroy.services.validation import latest_validation_report

__all__ = [
    "VariantError",
    "approve_variant",
    "append_variant_revision",
    "compare_variants",
    "create_variant_from_current",
    "delete_variant",
    "fork_variant",
    "get_variant",
    "latest_revision_for_variant",
    "list_variants",
    "patch_variant",
    "restore_variant_revision",
    "variant_lineage_ids",
    "variant_view",
]

# Allowed status transitions (same-status patches are no-ops).
STATUS_TRANSITIONS = {
    ("draft", "shortlisted"),
    ("shortlisted", "approved"),
    ("draft", "archived"),
    ("shortlisted", "archived"),
    ("approved", "archived"),
}

VALID_STATUSES = {"draft", "shortlisted", "approved", "archived"}


class VariantError(ValueError):
    """Service-level variant failure with an API mapping."""

    def __init__(self, detail: str, *, status_code: int = 422, code: str = "variant_error"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def variant_view(row: SceneVariantRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "title": row.title,
        "base_scene_revision_id": row.base_scene_revision_id,
        "head_scene_revision_id": row.head_scene_revision_id,
        "status": row.status,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_variant(
    session: AsyncSession, project_id: str, variant_id: str
) -> SceneVariantRow | None:
    row = await session.get(SceneVariantRow, variant_id)
    if row is None or row.project_id != project_id:
        return None
    return row


async def list_variants(
    session: AsyncSession,
    project_id: str,
    *,
    status: str | None = None,
    include_archived: bool = False,
) -> list[SceneVariantRow]:
    """Variants of a project, newest first.

    Default excludes archived rows; ``include_archived=True`` lifts that, and
    an explicit ``status`` filter (including ``status="archived"``) wins over
    the exclusion.
    """
    stmt = select(SceneVariantRow).where(SceneVariantRow.project_id == project_id)
    if status is not None:
        stmt = stmt.where(SceneVariantRow.status == status)
    elif not include_archived:
        stmt = stmt.where(SceneVariantRow.status != "archived")
    result = await session.execute(
        stmt.order_by(
            SceneVariantRow.created_at.desc(), SceneVariantRow.id.desc()
        )
    )
    return list(result.scalars())


async def create_variant_from_current(
    session: AsyncSession, project_id: str, title: str
) -> SceneVariantRow:
    """Seed a variant from the canonical scene's latest revision.

    The ONLY place the variant service reads the global latest revision:
    base = head = that revision; the canonical scene is untouched.
    """
    if await session.get(ProjectRow, project_id) is None:
        raise VariantError("project not found", status_code=404, code="project_not_found")
    base = await latest_revision(session, project_id)
    if base is None:
        raise CommandConflict("scene is not initialized")
    row = SceneVariantRow(
        project_id=project_id,
        title=title,
        base_scene_revision_id=base.id,
        head_scene_revision_id=base.id,
        status="draft",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def variant_lineage_ids(
    session: AsyncSession, head_revision_id: str
) -> list[str]:
    """Revision ids from ``head_revision_id`` up to the root (inclusive)."""
    lineage: list[str] = []
    current_id: str | None = head_revision_id
    seen: set[str] = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        lineage.append(current_id)
        row = await session.get(SceneRevisionRow, current_id)
        current_id = row.parent_revision_id if row else None
    return lineage


async def _require_in_lineage(
    session: AsyncSession, head_revision_id: str, revision_id: str, *, message: str
) -> None:
    lineage = await variant_lineage_ids(session, head_revision_id)
    if revision_id not in lineage:
        raise VariantError(message, status_code=409, code="revision_not_in_lineage")


async def fork_variant(
    session: AsyncSession,
    project_id: str,
    source_variant_id: str,
    *,
    title: str | None = None,
    from_revision_id: str | None = None,
) -> SceneVariantRow:
    """Fork a variant: a new branch pointing into the source's lineage.

    No revision rows are created or mutated. The new variant starts at
    ``from_revision_id`` (must lie in the source's parent-chain) or, without
    it, at the source's own base→head span.
    """
    source = await get_variant(session, project_id, source_variant_id)
    if source is None:
        return None  # type: ignore[return-value]
    base_id = source.base_scene_revision_id
    head_id = source.head_scene_revision_id
    if from_revision_id is not None:
        await _require_in_lineage(
            session,
            source.head_scene_revision_id,
            from_revision_id,
            message=(
                f"from_revision {from_revision_id} is not in the lineage of "
                f"variant {source_variant_id}"
            ),
        )
        base_id = from_revision_id
        head_id = from_revision_id
    row = SceneVariantRow(
        project_id=project_id,
        title=title if title is not None else f"{source.title} (fork)",
        base_scene_revision_id=base_id,
        head_scene_revision_id=head_id,
        status="draft",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def approve_variant(
    session: AsyncSession, project_id: str, variant_id: str
) -> SceneVariantRow:
    """Set the variant ``approved``, demoting any other approved variant.

    Single-approved invariant: the demotion and the promotion commit in ONE
    transaction, so a project can never observe zero-or-two approved states
    from this call.
    """
    row = await get_variant(session, project_id, variant_id)
    if row is None:
        return None  # type: ignore[return-value]
    if row.status != "shortlisted":
        raise VariantError(
            f"only shortlisted variants can be approved (status: {row.status})",
            status_code=422,
            code="invalid_status_transition",
        )
    others = await list_variants(session, project_id, status="approved")
    for other in others:
        if other.id != variant_id:
            other.status = "shortlisted"
            other.updated_at = utcnow()
    row.status = "approved"
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def patch_variant(
    session: AsyncSession,
    project_id: str,
    variant_id: str,
    updates: dict[str, Any],
) -> SceneVariantRow | None:
    """Patch title and/or status (state-machine validated)."""
    row = await get_variant(session, project_id, variant_id)
    if row is None:
        return None
    unknown = set(updates) - {"title", "status"}
    if unknown:
        raise VariantError(
            f"unknown variant fields: {', '.join(sorted(unknown))}",
            status_code=422,
            code="invalid_patch",
        )
    if "title" in updates:
        title = updates["title"]
        if not isinstance(title, str) or not title.strip():
            raise VariantError("title must be a non-empty string", code="invalid_patch")
        row.title = title
    if "status" in updates:
        new_status = updates["status"]
        if new_status not in VALID_STATUSES:
            raise VariantError(
                f"unknown variant status: {new_status}", code="invalid_status_transition"
            )
        if new_status == row.status:
            pass  # same-status patch is a no-op
        elif (row.status, new_status) not in STATUS_TRANSITIONS:
            raise VariantError(
                f"invalid status transition: {row.status} -> {new_status}",
                status_code=422,
                code="invalid_status_transition",
            )
        elif new_status == "approved":
            # Route through approve so the single-approved demotion happens
            # in the same transaction as the promotion.
            row.status = "shortlisted"  # satisfy approve's precondition
            await session.flush()
            return await approve_variant(session, project_id, variant_id)
        else:
            row.status = new_status
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def delete_variant(
    session: AsyncSession, project_id: str, variant_id: str
) -> bool:
    """Delete the variant ROW ONLY — revisions are never touched."""
    row = await get_variant(session, project_id, variant_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def latest_revision_for_variant(
    session: AsyncSession, variant: SceneVariantRow
) -> SceneRevisionRow | None:
    """The variant's head revision snapshot."""
    return await session.get(SceneRevisionRow, variant.head_scene_revision_id)


async def append_variant_revision(
    session: AsyncSession,
    project_id: str,
    variant_id: str,
    expected_head_revision_id: str,
    scene: Scene,
    *,
    command: DesignCommand | None = None,
    model_profile: str | None = None,
    correlation_id: str | None = None,
) -> SceneRevisionRow:
    """Append a revision to the variant and move its head.

    Optimistic-concurrency guard replaces the pre-0015 DB unique lock: the
    head update is conditional on the expected head; a lost race (or a stale
    expected head) rolls everything back and raises ``CommandConflict``.
    """
    variant = await get_variant(session, project_id, variant_id)
    if variant is None:
        raise VariantError("variant not found", status_code=404, code="variant_not_found")
    if variant.head_scene_revision_id != expected_head_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {variant.head_scene_revision_id}, "
            f"got {expected_head_revision_id}"
        )
    if command is not None:
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
                model_profile=model_profile,
                correlation_id=correlation_id,
            )
        )
    revision = SceneRevisionRow(
        project_id=project_id,
        parent_revision_id=expected_head_revision_id,
        command_id=command.command_id if command else None,
        content_hash=canonical_hash(scene),
        scene_json=scene.model_dump(mode="json", exclude_none=True),
    )
    session.add(revision)
    await session.flush()
    result = await session.execute(
        update(SceneVariantRow)
        .where(
            SceneVariantRow.id == variant_id,
            SceneVariantRow.head_scene_revision_id == expected_head_revision_id,
        )
        .values(head_scene_revision_id=revision.id, updated_at=utcnow())
    )
    if result.rowcount != 1:
        await session.rollback()
        raise CommandConflict("base revision was updated concurrently")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise CommandConflict("base revision was updated concurrently") from exc
    await session.refresh(revision)
    await session.refresh(variant)
    return revision


async def restore_variant_revision(
    session: AsyncSession,
    project_id: str,
    variant_id: str,
    *,
    target_revision_id: str,
    expected_head_revision_id: str,
) -> SceneRevisionRow:
    """Restore a revision's scene JSON as a NEW revision on the variant.

    The target must be in the variant's lineage; the canonical scene is never
    touched. Lineage violation → 409 ``revision_not_in_lineage``.
    """
    variant = await get_variant(session, project_id, variant_id)
    if variant is None:
        raise VariantError("variant not found", status_code=404, code="variant_not_found")
    if variant.head_scene_revision_id != expected_head_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {variant.head_scene_revision_id}, "
            f"got {expected_head_revision_id}"
        )
    target = await session.get(SceneRevisionRow, target_revision_id)
    if target is None or target.project_id != project_id:
        raise VariantError(
            f"target revision not found in project: {target_revision_id}",
            status_code=404,
            code="target_revision_not_found",
        )
    await _require_in_lineage(
        session,
        variant.head_scene_revision_id,
        target_revision_id,
        message=(
            f"target revision {target_revision_id} is not in the lineage of "
            f"variant {variant_id}"
        ),
    )
    scene = Scene.model_validate(target.scene_json)
    return await append_variant_revision(
        session,
        project_id,
        variant_id,
        expected_head_revision_id,
        scene,
    )


def _entity_field_diff(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """Changed leaf paths between two entity dumps, sorted."""
    changes: list[str] = []

    def walk(lv: Any, rv: Any, path: str) -> None:
        if isinstance(lv, dict) and isinstance(rv, dict):
            for key in sorted(set(lv) | set(rv)):
                walk(lv.get(key), rv.get(key), f"{path}.{key}" if path else key)
            return
        if lv != rv:
            changes.append(path)

    walk(left, right, "")
    return changes


def _warning_keys(report_json: dict[str, Any] | None) -> set[tuple[str, tuple[str, ...]]]:
    if not report_json:
        return set()
    keys = set()
    for result in report_json.get("results", []):
        if not isinstance(result, dict) or result.get("severity") != "warning":
            continue
        keys.add(
            (
                str(result.get("rule_id")),
                tuple(sorted(str(item) for item in result.get("entity_ids", []))),
            )
        )
    return keys


async def compare_variants(
    session: AsyncSession, project_id: str, left_id: str, right_id: str
) -> dict[str, Any]:
    """Deterministic diff of two variants' HEAD scenes."""
    left = await get_variant(session, project_id, left_id)
    if left is None:
        raise VariantError(f"variant not found: {left_id}", status_code=404, code="variant_not_found")
    right = await get_variant(session, project_id, right_id)
    if right is None:
        raise VariantError(f"variant not found: {right_id}", status_code=404, code="variant_not_found")

    left_revision = await latest_revision_for_variant(session, left)
    right_revision = await latest_revision_for_variant(session, right)
    left_scene = (
        Scene.model_validate(left_revision.scene_json) if left_revision else Scene(
            scene_id="scene.empty", project_id=project_id
        )
    )
    right_scene = (
        Scene.model_validate(right_revision.scene_json) if right_revision else Scene(
            scene_id="scene.empty", project_id=project_id
        )
    )

    left_entities = {entity.id: entity for entity in left_scene.entities}
    right_entities = {entity.id: entity for entity in right_scene.entities}
    added = sorted(set(right_entities) - set(left_entities))
    removed = sorted(set(left_entities) - set(right_entities))
    modified = []
    for entity_id in sorted(set(left_entities) & set(right_entities)):
        changes = _entity_field_diff(
            left_entities[entity_id].model_dump(mode="json", exclude_none=True),
            right_entities[entity_id].model_dump(mode="json", exclude_none=True),
        )
        if changes:
            modified.append({"id": entity_id, "changes": changes})

    left_materials = {e.material_ref for e in left_scene.entities if e.material_ref}
    right_materials = {e.material_ref for e in right_scene.entities if e.material_ref}

    left_report = await latest_validation_report(session, project_id, scene_revision_id=left.head_scene_revision_id) if left_revision else None
    right_report = await latest_validation_report(session, project_id, scene_revision_id=right.head_scene_revision_id) if right_revision else None
    left_warnings = _warning_keys(left_report.report_json if left_report else None)
    right_warnings = _warning_keys(right_report.report_json if right_report else None)

    def _warning_views(keys: set[tuple[str, tuple[str, ...]]]) -> list[dict[str, Any]]:
        return [
            {"rule_id": rule_id, "entity_ids": list(entity_ids)}
            for rule_id, entity_ids in sorted(keys)
        ]

    left_budget = await budget_report(session, project_id, left.id)
    right_budget = await budget_report(session, project_id, right.id)
    budget_delta: dict[str, Any] | None = None
    if not left_budget["incomplete"] and not right_budget["incomplete"]:
        budget_delta = {
            key: right_budget["totals"][key] - left_budget["totals"][key]
            for key in sorted(left_budget["totals"])
        }

    left_renders = (
        await session.execute(
            select(RenderManifestRow)
            .where(RenderManifestRow.variant_id == left.id)
            .order_by(RenderManifestRow.created_at.desc(), RenderManifestRow.id.desc())
        )
    ).scalars().all()
    right_renders = (
        await session.execute(
            select(RenderManifestRow)
            .where(RenderManifestRow.variant_id == right.id)
            .order_by(RenderManifestRow.created_at.desc(), RenderManifestRow.id.desc())
        )
    ).scalars().all()

    def _render_views(rows: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": row.id,
                "job_id": row.job_id,
                "scene_revision_id": row.scene_revision_id,
                "camera_id": row.camera_id,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    return {
        "left": _variant_ref(left),
        "right": _variant_ref(right),
        "entities": {"added": added, "removed": removed, "modified": modified},
        "materials": {
            "added": sorted(right_materials - left_materials),
            "removed": sorted(left_materials - right_materials),
        },
        "validation": {
            "added": _warning_views(right_warnings - left_warnings),
            "resolved": _warning_views(left_warnings - right_warnings),
        },
        "budget": {
            "left": _budget_summary(left_budget),
            "right": _budget_summary(right_budget),
            "delta": budget_delta,
        },
        "renders": {"left": _render_views(left_renders), "right": _render_views(right_renders)},
    }


def _variant_ref(row: SceneVariantRow) -> dict[str, Any]:
    return {
        "variant_id": row.id,
        "title": row.title,
        "status": row.status,
        "base_scene_revision_id": row.base_scene_revision_id,
        "head_scene_revision_id": row.head_scene_revision_id,
    }


def _budget_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "totals": report["totals"],
        "incomplete": report["incomplete"],
        "unknowns": report["unknowns"],
        "currency": report["currency"],
    }


async def apply_variant_command(
    session: AsyncSession,
    project_id: str,
    variant_id: str,
    command: DesignCommand,
    *,
    expected_head_revision_id: str,
    correlation_id: str | None = None,
):
    """Apply a design command to the variant HEAD scene.

    Reuses the existing command engine (``apply_command``): same operations,
    same lock/intent enforcement. The variant head is the base; the canonical
    scene is never read or written here.
    """
    variant = await get_variant(session, project_id, variant_id)
    if variant is None:
        raise VariantError("variant not found", status_code=404, code="variant_not_found")
    head = await latest_revision_for_variant(session, variant)
    if head is None:
        raise CommandConflict("variant head revision is missing")
    if head.id != command.base_revision_id:
        raise CommandConflict(
            f"stale base revision: expected {head.id}, got {command.base_revision_id}"
        )
    scene = Scene.model_validate(head.scene_json)
    next_scene = apply_command(scene, command)
    return await append_variant_revision(
        session,
        project_id,
        variant_id,
        expected_head_revision_id,
        next_scene,
        command=command,
        correlation_id=correlation_id,
    )
