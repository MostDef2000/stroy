"""Spatial validation service (R2): compute, persist, and fetch reports.

Validation never mutates the scene: it reads a ``SceneRevisionRow`` snapshot,
runs the pure spatial validator and stores the resulting report. Reports are
immutable rows keyed by (project, scene revision, config hash).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import SceneRevisionRow, ValidationReportRow
from stroy.domain.models import Scene
from stroy.services.scenes import latest_revision
from stroy.spatial import (
    ValidationConfig,
    config_hash as spatial_config_hash,
    report_hash as spatial_report_hash,
    scene_content_hash,
    validate_scene,
)

DEFAULT_MIN_WALKWAY_MM = 600.0


class ValidationConfigError(ValueError):
    """Raised when the validation config is invalid (HTTP 422)."""


class ValidationRevisionNotFoundError(ValueError):
    """Raised when the requested scene revision does not exist (HTTP 404)."""


def build_config(min_walkway_mm: float | None) -> ValidationConfig:
    if min_walkway_mm is None:
        return ValidationConfig()
    if not min_walkway_mm > 0:
        raise ValidationConfigError(
            f"min_walkway_mm must be greater than 0, got: {min_walkway_mm}"
        )
    return ValidationConfig(min_walkway_mm=min_walkway_mm)


async def resolve_revision(
    session: AsyncSession, project_id: str, scene_revision_id: str | None
) -> SceneRevisionRow:
    """Default to the latest revision; 404 on unknown/foreign revisions."""
    if scene_revision_id is None:
        revision = await latest_revision(session, project_id)
        if revision is None:
            raise ValidationRevisionNotFoundError("scene is not initialized")
        return revision
    revision = await session.get(SceneRevisionRow, scene_revision_id)
    if revision is None or revision.project_id != project_id:
        raise ValidationRevisionNotFoundError(
            f"scene revision not found in project: {scene_revision_id}"
        )
    return revision


def validation_view(row: ValidationReportRow) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "scene_revision_id": row.scene_revision_id,
        "scene_content_hash": row.scene_content_hash,
        "config_hash": row.config_hash,
        "report_hash": row.report_hash,
        "created_at": row.created_at,
        "report": row.report_json,
    }


async def run_validation(
    session: AsyncSession,
    project_id: str,
    *,
    scene_revision_id: str | None = None,
    min_walkway_mm: float | None = None,
) -> ValidationReportRow:
    """Compute a validation report for a revision and persist it.

    Never mutates the scene: only ``ValidationReportRow`` rows are written.
    """
    config = build_config(min_walkway_mm)
    revision = await resolve_revision(session, project_id, scene_revision_id)
    scene = Scene.model_validate(revision.scene_json)
    report = validate_scene(
        scene, scene_revision_id=revision.id, config=config
    )
    report_json = report.model_dump(mode="json")
    row = ValidationReportRow(
        project_id=project_id,
        scene_revision_id=revision.id,
        scene_content_hash=scene_content_hash(scene),
        config_hash=spatial_config_hash(config),
        report_hash=spatial_report_hash(report_json),
        report_json=report_json,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def latest_validation_report(
    session: AsyncSession,
    project_id: str,
    *,
    scene_revision_id: str | None = None,
    min_walkway_mm: float | None = None,
) -> ValidationReportRow | None:
    """Latest persisted report for the revision (default: latest) + config."""
    config = build_config(min_walkway_mm)
    revision = await resolve_revision(session, project_id, scene_revision_id)
    result = await session.execute(
        select(ValidationReportRow)
        .where(
            ValidationReportRow.project_id == project_id,
            ValidationReportRow.scene_revision_id == revision.id,
            ValidationReportRow.config_hash == spatial_config_hash(config),
        )
        .order_by(
            ValidationReportRow.created_at.desc(), ValidationReportRow.id.desc()
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def execute_design_check_tool(
    session: AsyncSession,
    project_id: str,
    *,
    scene_revision_id: str | None = None,
    min_walkway_mm: float | None = None,
) -> dict:
    """Agent read tool: return the persisted latest report or compute one."""
    row = await latest_validation_report(
        session,
        project_id,
        scene_revision_id=scene_revision_id,
        min_walkway_mm=min_walkway_mm,
    )
    if row is not None:
        return validation_view(row)
    row = await run_validation(
        session,
        project_id,
        scene_revision_id=scene_revision_id,
        min_walkway_mm=min_walkway_mm,
    )
    return validation_view(row)
