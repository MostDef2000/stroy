from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import AssetRow, JobRow, StyleProfileRow
from stroy.style import StyleProfile, StyleProfileProposal, merge_style_overrides
from stroy.style.models import StyleOverrides


async def create_style_profile_from_job(
    session: AsyncSession,
    job: JobRow,
    result: dict[str, Any],
) -> StyleProfileRow:
    if job.job_type != "style.analyze":
        raise ValueError("job is not a style analysis job")
    if not job.project_id:
        raise ValueError("style analysis job has no project")

    proposal_raw = result.get("style_profile")
    if not isinstance(proposal_raw, dict):
        raise ValueError("style analysis result requires style_profile object")
    proposal = StyleProfileProposal.model_validate(proposal_raw)

    overrides_raw = job.payload.get("overrides")
    overrides = (
        StyleOverrides.model_validate(overrides_raw)
        if isinstance(overrides_raw, dict)
        else None
    )
    merged = merge_style_overrides(proposal, overrides)

    source_asset_ids = list(job.payload.get("input_asset_ids") or [])
    for asset_id in source_asset_ids:
        asset = await session.get(AssetRow, asset_id)
        if asset is None or asset.project_id != job.project_id:
            raise ValueError(f"style source asset is not in project: {asset_id}")

    profile_id = str(uuid4())
    profile = StyleProfile(
        style_profile_id=profile_id,
        source_asset_ids=source_asset_ids,
        source_text=job.payload.get("source_text"),
        labels=merged.labels,
        palette=merged.palette,
        materials=merged.materials,
        lighting=merged.lighting,
        forms=merged.forms,
        negative_constraints=merged.negative_constraints,
        metadata={
            "evidence": merged.evidence,
            "job_id": job.id,
            "model_profile": job.payload.get("model_profile"),
        },
    )

    row = StyleProfileRow(
        id=profile_id,
        project_id=job.project_id,
        schema_version=profile.schema_version,
        source_asset_ids=source_asset_ids,
        source_text=profile.source_text,
        profile_json=profile.model_dump(mode="json", exclude_none=True),
        model_profile=job.payload.get("model_profile"),
        correlation_id=job.correlation_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_style_profiles(
    session: AsyncSession,
    project_id: str,
) -> list[StyleProfileRow]:
    result = await session.execute(
        select(StyleProfileRow)
        .where(StyleProfileRow.project_id == project_id)
        .order_by(StyleProfileRow.created_at.desc(), StyleProfileRow.id.desc())
    )
    return list(result.scalars())
