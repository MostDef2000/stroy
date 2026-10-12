"""Renovation progress snapshots + planned-vs-actual deviations (R12, #162).

A snapshot is a named site observation pinned ONCE to an immutable scene
revision — the canonical latest (canonical kind) or an approved variant's
head (variant kind). The pinned ``compare_target_revision_id`` never
re-resolves to "latest": compare and accept always operate against the pin.

Deviations are owner-recorded (planned-vs-actual notes); there is NO photo
inference here. ``planned_revision_id`` is copied from the snapshot's pin at
creation, and planned entity/room ids are FK-less strings — dangling ids are
allowed and surfaced as ``dangling`` by compare. Progress data lives entirely
outside ``scene_json`` (canonical_hash semantics untouched).

Deviation status machine: open→confirmed, open→discarded, confirmed→open,
confirmed→discarded, discarded→open. ``accepted`` is immutable and reachable
ONLY through :func:`accept_deviation` (which applies the confirmed fix as a
real scene command — canonical CAS or expected-head variant CAS — and then
marks the deviation in a second commit; a failed second commit leaves the
deviation confirmed, so the accept is retry-safe).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import (
    AttachmentRow,
    DeviationRow,
    ProgressSnapshotRow,
    ProjectRow,
    SceneRevisionRow,
    utcnow,
)
from stroy.domain.models import CommandOperation, DesignCommand, EntityKind, Scene
from stroy.services.scenes import apply_scene_command, latest_revision
from stroy.services.variants import apply_variant_command, get_variant

__all__ = [
    "CONFIDENCES",
    "DEVIATION_PROVENANCES",
    "DEVIATION_STATUS_TRANSITIONS",
    "OBSERVED_KINDS",
    "SNAPSHOT_PROVENANCES",
    "SNAPSHOT_STAGES",
    "ProgressError",
    "accept_deviation",
    "compare_snapshot",
    "create_deviation",
    "create_snapshot",
    "deviation_counts",
    "deviation_view",
    "get_deviation",
    "get_snapshot",
    "list_snapshot_deviations",
    "list_snapshots",
    "patch_deviation",
    "snapshot_view",
    "soft_delete_snapshot",
]

# Fixed vocabularies (contract-approved; no other values accepted).
SNAPSHOT_STAGES = frozenset(
    {"original", "demolition", "rough", "fitout", "current", "completed"}
)
SNAPSHOT_PROVENANCES = frozenset(
    {"observed", "manually_measured", "model_inferred", "confirmed"}
)
OBSERVED_KINDS = frozenset({"missing", "unexpected", "property_diff", "state_diff", "note"})
DEVIATION_PROVENANCES = frozenset(
    {"observed", "manually_measured", "model_inferred", "confirmed"}
)
CONFIDENCES = frozenset({"low", "medium", "high"})

# Status machine: same-status patches are no-ops; accepted rows are patched
# by nobody (the accept endpoint is the only writer of accepted).
DEVIATION_STATUS_TRANSITIONS = {
    ("open", "confirmed"),
    ("open", "discarded"),
    ("confirmed", "open"),
    ("confirmed", "discarded"),
    ("discarded", "open"),
}
DEVIATION_STATUSES = frozenset({"open", "confirmed", "accepted", "discarded"})
# accepted cannot be chosen at creation (only via the accept endpoint);
# open/confirmed/discarded may be recorded directly.
CREATABLE_DEVIATION_STATUSES = frozenset({"open", "confirmed", "discarded"})


class ProgressError(ValueError):
    """Service-level progress failure with an API mapping."""

    def __init__(self, detail: str, *, status_code: int = 422, code: str = "progress_error"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def snapshot_view(row: ProgressSnapshotRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "stage": row.stage,
        "stage_label": row.stage_label,
        "captured_at": row.captured_at,
        "provenance": row.provenance,
        "note": row.note,
        "attachment_ids": row.attachment_ids,
        "compare_target": {
            "kind": row.compare_target_kind,
            "variant_id": row.compare_target_variant_id,
            "revision_id": row.compare_target_revision_id,
        },
        "metadata": row.metadata_json,
        "deleted_at": row.deleted_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def deviation_view(row: DeviationRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "snapshot_id": row.snapshot_id,
        "status": row.status,
        "planned_revision_id": row.planned_revision_id,
        "planned": {
            "target_type": row.planned_target_type,
            "entity_id": row.planned_entity_id,
            "room_id": row.planned_room_id,
            "property_path": row.planned_property_path,
            "value": row.planned_value_json,
        },
        "observed": {
            "kind": row.observed_kind,
            "value": row.observed_value_json,
        },
        "provenance": row.provenance,
        "confidence": row.confidence,
        "evidence_attachment_ids": row.evidence_attachment_ids,
        "note": row.note,
        "accepted_target_kind": row.accepted_target_kind,
        "accepted_variant_id": row.accepted_variant_id,
        "accepted_command_id": row.accepted_command_id,
        "accepted_revision_id": row.accepted_revision_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_snapshot(
    session: AsyncSession,
    project_id: str,
    snapshot_id: str,
    *,
    include_deleted: bool = False,
) -> ProgressSnapshotRow | None:
    row = await session.get(ProgressSnapshotRow, snapshot_id)
    if row is None or row.project_id != project_id:
        return None
    if row.deleted_at is not None and not include_deleted:
        return None
    return row


async def list_snapshots(
    session: AsyncSession,
    project_id: str,
    *,
    stage: str | None = None,
    include_deleted: bool = False,
) -> list[ProgressSnapshotRow]:
    """Snapshots of a project, newest first. Deleted rows hidden unless
    ``include_deleted``; an explicit ``stage`` filter narrows further."""
    stmt = select(ProgressSnapshotRow).where(
        ProgressSnapshotRow.project_id == project_id
    )
    if not include_deleted:
        stmt = stmt.where(ProgressSnapshotRow.deleted_at.is_(None))
    if stage is not None:
        stmt = stmt.where(ProgressSnapshotRow.stage == stage)
    result = await session.execute(
        stmt.order_by(
            ProgressSnapshotRow.created_at.desc(), ProgressSnapshotRow.id.desc()
        )
    )
    return list(result.scalars())


async def deviation_counts(
    session: AsyncSession, project_id: str, snapshot_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Per-snapshot deviation counts by status: {snapshot_id: {status: n}}.

    Snapshots without deviations get an all-zero board, so every listed
    snapshot carries the full {open, confirmed, accepted, discarded} shape.
    """
    boards: dict[str, dict[str, int]] = {
        snapshot_id: {status: 0 for status in sorted(DEVIATION_STATUSES)}
        for snapshot_id in snapshot_ids
    }
    if not snapshot_ids:
        return boards
    result = await session.execute(
        select(DeviationRow.snapshot_id, DeviationRow.status, func.count())
        .where(
            DeviationRow.project_id == project_id,
            DeviationRow.snapshot_id.in_(snapshot_ids),
        )
        .group_by(DeviationRow.snapshot_id, DeviationRow.status)
    )
    for snapshot_id, status, count in result.all():
        if snapshot_id in boards and status in boards[snapshot_id]:
            boards[snapshot_id][status] = count
    return boards


async def _validate_project_attachments(
    session: AsyncSession, project_id: str, attachment_ids: list[str]
) -> None:
    """Attachment ids must reference attachment rows of THIS project (404
    otherwise, IDOR-safe). FK-less by design: attachment deletion never
    cascades into snapshots/deviations."""
    for attachment_id in attachment_ids:
        row = await session.get(AttachmentRow, attachment_id)
        if row is None or row.project_id != project_id:
            raise ProgressError(
                f"attachment not found in project: {attachment_id}",
                status_code=404,
                code="attachment_not_found",
            )


async def _pin_compare_target(
    session: AsyncSession, project_id: str, compare_target: dict[str, Any] | None
) -> tuple[str, str | None, str]:
    """Resolve the snapshot's pinned revision ONCE at creation time.

    Omitted (or canonical) → the canonical scene's latest revision; missing
    scene → 404 ``scene_not_initialized`` (never lazily re-resolved later).
    Variant → must belong to the project AND be approved; its head revision
    is pinned.
    """
    kind = (compare_target or {}).get("kind", "canonical")
    if kind == "variant":
        variant_id = (compare_target or {}).get("variant_id")
        variant = await get_variant(session, project_id, variant_id)
        if variant is None:
            raise ProgressError(
                f"variant not found in project: {variant_id}",
                status_code=404,
                code="variant_not_found",
            )
        if variant.status != "approved":
            raise ProgressError(
                f"variant {variant_id} is not approved (status: {variant.status})",
                status_code=422,
                code="variant_not_approved",
            )
        return "variant", variant.id, variant.head_scene_revision_id
    if kind != "canonical":
        raise ProgressError(
            f"unknown compare target kind: {kind}", code="invalid_compare_target"
        )
    revision = await latest_revision(session, project_id)
    if revision is None:
        raise ProgressError(
            "scene is not initialized", status_code=404, code="scene_not_initialized"
        )
    return "canonical", None, revision.id


async def create_snapshot(
    session: AsyncSession, project_id: str, data: dict[str, Any]
) -> ProgressSnapshotRow:
    """Create a snapshot with its compare target pinned."""
    if await session.get(ProjectRow, project_id) is None:
        raise ProgressError("project not found", status_code=404, code="project_not_found")
    stage = data.get("stage")
    if stage not in SNAPSHOT_STAGES:
        raise ProgressError(f"unknown stage: {stage}", code="invalid_stage")
    provenance = data.get("provenance") or "observed"
    if provenance not in SNAPSHOT_PROVENANCES:
        raise ProgressError(f"unknown provenance: {provenance}", code="invalid_provenance")

    target_kind, target_variant_id, target_revision_id = await _pin_compare_target(
        session, project_id, data.get("compare_target")
    )
    attachment_ids = list(data.get("attachment_ids") or [])
    await _validate_project_attachments(session, project_id, attachment_ids)

    row = ProgressSnapshotRow(
        project_id=project_id,
        name=data["name"],
        stage=stage,
        stage_label=data.get("stage_label"),
        captured_at=data["captured_at"],
        provenance=provenance,
        note=data.get("note"),
        attachment_ids=attachment_ids,
        compare_target_kind=target_kind,
        compare_target_variant_id=target_variant_id,
        compare_target_revision_id=target_revision_id,
        # Column kept for future owner notes; nothing writes it in R12.
        metadata_json={},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def soft_delete_snapshot(
    session: AsyncSession, project_id: str, snapshot_id: str
) -> bool:
    """Soft delete: stamps ``deleted_at``; attachments/deviations untouched."""
    row = await get_snapshot(session, project_id, snapshot_id)
    if row is None:
        return False
    row.deleted_at = utcnow()
    row.updated_at = utcnow()
    await session.commit()
    return True


async def get_deviation(
    session: AsyncSession, project_id: str, deviation_id: str
) -> DeviationRow | None:
    row = await session.get(DeviationRow, deviation_id)
    if row is None or row.project_id != project_id:
        return None
    return row


async def list_snapshot_deviations(
    session: AsyncSession, project_id: str, snapshot_id: str
) -> list[DeviationRow]:
    result = await session.execute(
        select(DeviationRow)
        .where(
            DeviationRow.project_id == project_id,
            DeviationRow.snapshot_id == snapshot_id,
        )
        .order_by(DeviationRow.created_at.asc(), DeviationRow.id.asc())
    )
    return list(result.scalars())


async def create_deviation(
    session: AsyncSession,
    project_id: str,
    snapshot: ProgressSnapshotRow,
    data: dict[str, Any],
) -> DeviationRow:
    """Record an owner-observed deviation against a snapshot.

    ``planned_revision_id`` is copied from the snapshot's pinned target.
    Dangling planned entity/room ids are allowed (dangling is surfaced by
    compare, never rejected). ``accepted`` status is NOT creatable.
    """
    observed = data.get("observed") or {}
    if observed.get("kind") not in OBSERVED_KINDS:
        raise ProgressError(
            f"unknown observed kind: {observed.get('kind')}", code="invalid_observed_kind"
        )
    if observed.get("value") is None:
        raise ProgressError(
            "observed.value is required", status_code=422, code="observed_value_required"
        )
    provenance = data.get("provenance")
    if provenance not in DEVIATION_PROVENANCES:
        raise ProgressError(
            f"unknown provenance: {provenance}", code="invalid_provenance"
        )
    confidence = data.get("confidence")
    if confidence not in CONFIDENCES:
        raise ProgressError(f"unknown confidence: {confidence}", code="invalid_confidence")
    status = data.get("status") or "open"
    if status == "accepted":
        raise ProgressError(
            "deviations cannot be created accepted; use the accept endpoint",
            status_code=422,
            code="accepted_not_allowed",
        )
    if status not in CREATABLE_DEVIATION_STATUSES:
        raise ProgressError(f"unknown status: {status}", code="invalid_status")

    planned = data.get("planned") or {}
    planned_target_type = planned.get("target_type")
    if planned_target_type not in {"scene", "room", "entity"}:
        raise ProgressError(
            f"unknown planned target_type: {planned_target_type}",
            code="invalid_planned_target_type",
        )
    evidence_attachment_ids = list(data.get("evidence_attachment_ids") or [])
    await _validate_project_attachments(session, project_id, evidence_attachment_ids)

    row = DeviationRow(
        project_id=project_id,
        snapshot_id=snapshot.id,
        status=status,
        planned_revision_id=snapshot.compare_target_revision_id,
        planned_target_type=planned_target_type,
        planned_entity_id=planned.get("entity_id"),
        planned_room_id=planned.get("room_id"),
        planned_property_path=planned.get("property_path"),
        planned_value_json=planned.get("value"),
        observed_kind=observed["kind"],
        observed_value_json=observed.get("value"),
        provenance=provenance,
        confidence=confidence,
        evidence_attachment_ids=evidence_attachment_ids,
        note=data.get("note"),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def patch_deviation(
    session: AsyncSession,
    project_id: str,
    deviation_id: str,
    updates: dict[str, Any],
) -> DeviationRow | None:
    """Patch status/provenance/confidence/observed/note (transition table).

    Accepted rows are immutable — any PATCH on them is a 409.
    """
    row = await get_deviation(session, project_id, deviation_id)
    if row is None:
        return None
    unknown = set(updates) - {"status", "provenance", "confidence", "observed", "note"}
    if unknown:
        raise ProgressError(
            f"unknown deviation fields: {', '.join(sorted(unknown))}",
            code="invalid_patch",
        )
    if row.status == "accepted":
        raise ProgressError(
            "accepted deviations are immutable",
            status_code=409,
            code="accepted_immutable",
        )
    if "provenance" in updates:
        if updates["provenance"] not in DEVIATION_PROVENANCES:
            raise ProgressError(
                f"unknown provenance: {updates['provenance']}", code="invalid_provenance"
            )
        row.provenance = updates["provenance"]
    if "confidence" in updates:
        if updates["confidence"] not in CONFIDENCES:
            raise ProgressError(
                f"unknown confidence: {updates['confidence']}", code="invalid_confidence"
            )
        row.confidence = updates["confidence"]
    if "observed" in updates:
        observed = updates["observed"] or {}
        if observed.get("kind") not in OBSERVED_KINDS:
            raise ProgressError(
                f"unknown observed kind: {observed.get('kind')}",
                code="invalid_observed_kind",
            )
        if observed.get("value") is None:
            raise ProgressError(
                "observed.value is required",
                status_code=422,
                code="observed_value_required",
            )
        row.observed_kind = observed["kind"]
        row.observed_value_json = observed.get("value")
    if "note" in updates:
        row.note = updates["note"]
    if "status" in updates:
        new_status = updates["status"]
        if new_status not in DEVIATION_STATUSES:
            raise ProgressError(
                f"unknown deviation status: {new_status}", code="invalid_status"
            )
        if new_status == "accepted":
            raise ProgressError(
                "accepted is reachable only via the accept endpoint",
                status_code=409,
                code="accepted_not_allowed",
            )
        if new_status != row.status and (
            row.status,
            new_status,
        ) not in DEVIATION_STATUS_TRANSITIONS:
            raise ProgressError(
                f"invalid status transition: {row.status} -> {new_status}",
                status_code=409,
                code="invalid_status_transition",
            )
        row.status = new_status
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return row


async def _pinned_revision(
    session: AsyncSession, project_id: str, snapshot: ProgressSnapshotRow
) -> SceneRevisionRow:
    """The snapshot's pinned revision row (404 when it vanished)."""
    revision = await session.get(SceneRevisionRow, snapshot.compare_target_revision_id)
    if revision is None or revision.project_id != project_id:
        raise ProgressError(
            f"pinned revision not found in project: {snapshot.compare_target_revision_id}",
            status_code=404,
            code="target_revision_not_found",
        )
    return revision


def _planned_dangling(deviation: DeviationRow, scene: Scene) -> bool:
    """True when the planned entity/room id is absent from the pinned scene."""
    if deviation.planned_target_type == "entity":
        entity_ids = {entity.id for entity in scene.entities}
        return deviation.planned_entity_id is not None and (
            deviation.planned_entity_id not in entity_ids
        )
    if deviation.planned_target_type == "room":
        room_ids = {
            entity.id for entity in scene.entities if entity.kind is EntityKind.ROOM
        }
        return deviation.planned_room_id is not None and (
            deviation.planned_room_id not in room_ids
        )
    return False


async def compare_snapshot(
    session: AsyncSession, project_id: str, snapshot: ProgressSnapshotRow
) -> dict[str, Any]:
    """Snapshot summary + deviations against the PINNED revision's scene.

    Deterministic and read-only: the pinned revision is never re-resolved and
    the scene is never mutated (no fake scene JSON for snapshots — deviations
    carry their own planned/observed payloads).
    """
    revision = await _pinned_revision(session, project_id, snapshot)
    scene = Scene.model_validate(revision.scene_json)
    deviations = await list_snapshot_deviations(session, project_id, snapshot.id)

    summary = {status: 0 for status in sorted(DEVIATION_STATUSES)}
    deviation_views: list[dict[str, Any]] = []
    for deviation in deviations:
        summary[deviation.status] = summary.get(deviation.status, 0) + 1
        planned = {
            "target_type": deviation.planned_target_type,
            "entity_id": deviation.planned_entity_id,
            "room_id": deviation.planned_room_id,
            "property_path": deviation.planned_property_path,
            "value": deviation.planned_value_json,
            "dangling": _planned_dangling(deviation, scene),
        }
        deviation_views.append(
            {
                "id": deviation.id,
                "status": deviation.status,
                "planned": planned,
                "observed": {
                    "kind": deviation.observed_kind,
                    "value": deviation.observed_value_json,
                },
                "provenance": deviation.provenance,
                "confidence": deviation.confidence,
                "evidence_attachment_ids": deviation.evidence_attachment_ids,
                "note": deviation.note,
            }
        )

    return {
        "snapshot": snapshot_view(snapshot),
        "target": {
            "kind": snapshot.compare_target_kind,
            "variant_id": snapshot.compare_target_variant_id,
            "revision_id": snapshot.compare_target_revision_id,
        },
        "summary": {**summary, "total": len(deviations)},
        "deviations": deviation_views,
    }


def _validate_accept_envelope(
    deviation_id: str, command: DesignCommand
) -> DesignCommand:
    """Shape-validate the accept command envelope (422 on violation)."""
    parameters = command.parameters or {}
    if parameters.get("deviation_id") != deviation_id:
        raise ProgressError(
            "command.parameters.deviation_id does not match the path deviation",
            code="invalid_accept_envelope",
        )
    raw_change = parameters.get("change")
    if not isinstance(raw_change, dict):
        raise ProgressError(
            "command.parameters.change must be a command object",
            code="invalid_accept_envelope",
        )
    try:
        change = DesignCommand.model_validate(raw_change)
    except ValueError as exc:
        raise ProgressError(
            f"command.parameters.change is not a valid command: {exc}",
            code="invalid_accept_envelope",
        ) from exc
    if change.operation is CommandOperation.ACCEPT_CONFIRMED_DEVIATION:
        raise ProgressError(
            "embedded change must not be accept_confirmed_deviation",
            code="invalid_accept_envelope",
        )
    # The wrapper must actually be an accept command and carry the same
    # deviation — the domain engine re-validates the shape, this pins the
    # linkage so a stray command cannot ride the accept endpoint.
    if command.operation is not CommandOperation.ACCEPT_CONFIRMED_DEVIATION:
        raise ProgressError(
            "command.operation must be accept_confirmed_deviation",
            code="invalid_accept_envelope",
        )
    # Keep the accepted artifact faithful to what was applied: the embedded
    # change must be executable as-is (locks/intents reject like any command).
    return change


async def accept_deviation(
    session: AsyncSession,
    project_id: str,
    deviation_id: str,
    *,
    target_kind: str,
    variant_id: str | None = None,
    expected_head_revision_id: str | None = None,
    command: DesignCommand,
) -> tuple[DeviationRow, SceneRevisionRow]:
    """Apply a confirmed deviation's fix as a real scene command, then mark
    the deviation accepted.

    Guards: deviation in project (404), status confirmed (409), envelope
    shape (422). Canonical → :func:`apply_scene_command` (CAS against the
    canonical latest, as-is); variant → variant in project + approved + not
    archived + :func:`apply_variant_command` with expected-head CAS.

    ``apply_*`` commit internally, so the deviation update lands in a second
    commit right after; if that second commit fails the deviation stays
    confirmed and the accept can be retried with the same command.

    Retry semantics (residual risk, accepted for v1 — documented, not
    engineered): retrying with the SAME command_id after a successful
    commit-1 fails cleanly with 409 ``revision_conflict`` (the CAS head has
    moved). Retrying with a NEW command_id after commit-1 succeeded but
    commit-2 failed WOULD double-apply the embedded change (a new revision
    on top of the first); that requires a mid-request DB failure between
    the two commits and is out of scope for v1.
    """
    deviation = await get_deviation(session, project_id, deviation_id)
    if deviation is None:
        raise ProgressError(
            f"deviation not found in project: {deviation_id}",
            status_code=404,
            code="deviation_not_found",
        )
    if deviation.status != "confirmed":
        raise ProgressError(
            f"only confirmed deviations can be accepted (status: {deviation.status})",
            status_code=409,
            code="deviation_not_confirmed",
        )
    _validate_accept_envelope(deviation_id, command)

    if target_kind == "canonical":
        revision = await apply_scene_command(session, project_id, command)
        accepted_variant_id = None
    elif target_kind == "variant":
        variant = await get_variant(session, project_id, variant_id)
        if variant is None:
            raise ProgressError(
                f"variant not found in project: {variant_id}",
                status_code=404,
                code="variant_not_found",
            )
        if variant.status == "archived":
            raise ProgressError(
                "variant is archived", status_code=409, code="variant_archived"
            )
        if variant.status != "approved":
            raise ProgressError(
                f"variant {variant_id} is not approved (status: {variant.status})",
                status_code=409,
                code="variant_not_approved",
            )
        revision = await apply_variant_command(
            session,
            project_id,
            variant_id,
            command,
            expected_head_revision_id=expected_head_revision_id,
        )
        accepted_variant_id = variant_id
    else:
        raise ProgressError(
            f"unknown accept target kind: {target_kind}", code="invalid_accept_target"
        )

    deviation.status = "accepted"
    deviation.accepted_target_kind = target_kind
    deviation.accepted_variant_id = accepted_variant_id
    deviation.accepted_command_id = command.command_id
    deviation.accepted_revision_id = revision.id
    deviation.updated_at = utcnow()
    try:
        await session.commit()
    except Exception:
        # Retry-safe: the applied revision stays; the deviation stays
        # confirmed and the accept can be re-run with the same command.
        await session.rollback()
        raise
    await session.refresh(deviation)
    return deviation, revision
