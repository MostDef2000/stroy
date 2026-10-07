from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import (
    AssetRow,
    AttachmentRow,
    BudgetItemRow,
    DesignCommandRow,
    GenerationManifestRow,
    GeometryDiagnosticRow,
    JobRow,
    PlanDraftRow,
    ProductCandidateRow,
    ProjectRow,
    RenderManifestRow,
    SceneRevisionRow,
    SceneVariantRow,
    StyleProfileRow,
    ValidationReportRow,
)
from stroy.services.jobs import TERMINAL_JOB_STATUSES


class ProjectHasActiveJobsError(ValueError):
    """Raised when a project still owns queued/running jobs."""


# Project-scoped tables, ordered children-first so every row is removed before
# the rows/tables it references.  ``project_id`` is the only declared foreign
# key in ``stroy.db.models`` (no ON DELETE CASCADE), so the project row must be
# deleted last.  The remaining entries are ordered by the references implied by
# the schema's string columns; no such constraints exist today, but keeping the
# order correct makes the cascade safe if they are ever promoted to real FKs.
#   design_commands.base_revision_id              -> scene_revisions.id
#   generation_manifests.scene_revision_id        -> scene_revisions.id
#   generation_manifests.job_id                   -> jobs.id
#   render_manifests.scene_revision_id            -> scene_revisions.id
#   render_manifests.job_id                       -> jobs.id
#   geometry_diagnostics.scene_revision_id        -> scene_revisions.id
#   geometry_diagnostics.job_id                   -> jobs.id
#   geometry_diagnostics.reference/generated_asset_id -> assets.id
#   plan_drafts.job_id                            -> jobs.id
#   attachments.asset_id                          -> assets.id
#   product_candidates.source_asset_id            -> assets.id
#   product_candidates.preview_asset_id           -> assets.id
#   validation_reports.scene_revision_id          -> scene_revisions.id
#   budget_items.product_candidate_id             -> product_candidates.id
#   budget_items.variant_id                       -> scene_variants.id (string)
#   budget_items.scene_revision_id                -> scene_revisions.id (string)
#   scene_variants.project_id                     -> projects.id
#   scene_variants.base/head_scene_revision_id    -> scene_revisions.id (string)
#   assets.source_asset_id / duplicate_of_asset_id -> assets.id (self)
#   scene_revisions.parent_revision_id            -> scene_revisions.id (self)
_CHILD_TABLES = (
    StyleProfileRow,
    DesignCommandRow,
    PlanDraftRow,
    GenerationManifestRow,
    RenderManifestRow,
    GeometryDiagnosticRow,
    AttachmentRow,
    # Budget items carry a real FK to product_candidates, so they must be
    # removed BEFORE the candidates they reference (R4).
    BudgetItemRow,
    # Candidates carry real FKs to assets (source_asset_id / preview_asset_id),
    # so they must be removed BEFORE the assets they reference.
    ProductCandidateRow,
    AssetRow,
    ValidationReportRow,
    # Variants reference revisions via string columns only, but stay ahead of
    # scene_revisions to keep the cascade order correct if they are ever
    # promoted to real FKs (R4).
    SceneVariantRow,
    SceneRevisionRow,
    JobRow,
)


async def active_job_count(session: AsyncSession, project_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(JobRow)
        .where(
            JobRow.project_id == project_id,
            JobRow.status.notin_(TERMINAL_JOB_STATUSES),
        )
    )
    return result.scalar_one()


async def delete_project(session: AsyncSession, project_id: str) -> bool:
    """Delete a project and every project-scoped row.

    Returns ``False`` when the project does not exist (idempotent 404 signal).
    Raises :class:`ProjectHasActiveJobsError` when non-terminal jobs block the
    cascade.  The whole cascade commits atomically.
    """
    project = await session.get(ProjectRow, project_id)
    if project is None:
        return False

    active = await active_job_count(session, project_id)
    if active:
        raise ProjectHasActiveJobsError(
            f"project has {active} queued/running job(s)"
        )

    for table in _CHILD_TABLES:
        await session.execute(delete(table).where(table.project_id == project_id))
    await session.delete(project)
    await session.commit()
    return True
