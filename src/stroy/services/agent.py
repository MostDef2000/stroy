from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from stroy.agent import (
    AgentToolError,
    CONTROL_TOOL_NAMES,
    MUTATION_TOOL_NAMES,
    READ_TOOL_NAMES,
    execute_read_tool,
    tool_call_to_command,
    validate_tool_call,
)
from stroy.db.models import JobRow
from stroy.domain.commands import CommandConflict, apply_command
from stroy.domain.models import Scene
from stroy.services.dispatch import JobDispatcher
from stroy.services.generations import queue_design_generation
from stroy.services.jobs import create_job
from stroy.services.products import candidate_view, get_candidate, list_candidates
from stroy.services.scenes import apply_scene_command, latest_revision
from stroy.services.validation import execute_design_check_tool
from stroy.services.variants import (
    VariantError,
    compare_variants,
    get_variant,
    list_variants,
    variant_view,
)
from stroy.services.budget import BudgetError, budget_report


async def apply_design_agent_result(
    session: AsyncSession,
    job: JobRow,
    result: dict[str, Any],
    *,
    dispatcher: JobDispatcher | None = None,
) -> dict[str, Any]:
    """Validate and execute the constrained tool proposal returned by the LLM."""
    if job.job_type != "llm.complete" or job.payload.get("purpose") != "design_instruction":
        return result

    if not job.project_id:
        raise CommandConflict("design instruction job has no project")

    base_revision_id = job.payload.get("base_revision_id")
    request_text = job.payload.get("request_text")
    current = await latest_revision(session, job.project_id)
    if current is None:
        raise CommandConflict("scene is not initialized")
    if current.id != base_revision_id:
        raise CommandConflict(
            f"scene changed while agent was running: expected {base_revision_id}, got {current.id}"
        )

    calls = result.get("tool_calls")
    if calls is None:
        calls = []
    if not isinstance(calls, list):
        raise ValueError("LLM result tool_calls must be a list")

    validation_scene = Scene.model_validate(current.scene_json)
    commands = []
    affected_entity_ids: list[str] = []
    referenced_asset_ids: list[str] = []
    command_summary: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    preview_requests: list[dict[str, Any]] = []
    revision_marker_calls: list[int] = []

    for index, raw_call in enumerate(calls):
        if not isinstance(raw_call, dict):
            raise ValueError("LLM tool call must be an object")
        name, parsed = validate_tool_call(raw_call)

        if name in READ_TOOL_NAMES:
            if name == "get_design_check":
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": await execute_design_check_tool(
                            session,
                            job.project_id,
                            scene_revision_id=parsed.scene_revision_id,  # type: ignore[attr-defined]
                            min_walkway_mm=parsed.min_walkway_mm,  # type: ignore[attr-defined]
                        ),
                    }
                )
                continue
            if name == "list_product_candidates":
                rows = await list_candidates(
                    session,
                    job.project_id,
                    source=parsed.source,  # type: ignore[attr-defined]
                )
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": {"candidates": [candidate_view(row) for row in rows]},
                    }
                )
                continue
            if name == "get_product_candidate":
                candidate_id = parsed.candidate_id  # type: ignore[attr-defined]
                row = await get_candidate(session, job.project_id, candidate_id)
                if row is None:
                    raise AgentToolError(
                        "unknown_entity",
                        f"unknown product candidate: {candidate_id}",
                        context={"candidate_id": candidate_id},
                    )
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": {"candidate": candidate_view(row)},
                    }
                )
                continue
            if name == "list_variants":
                rows = await list_variants(
                    session,
                    job.project_id,
                    status=parsed.status,  # type: ignore[attr-defined]
                    include_archived=parsed.include_archived,  # type: ignore[attr-defined]
                )
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": {"variants": [variant_view(row) for row in rows]},
                    }
                )
                continue
            if name == "get_variant":
                variant_id = parsed.variant_id  # type: ignore[attr-defined]
                row = await get_variant(session, job.project_id, variant_id)
                if row is None:
                    raise AgentToolError(
                        "unknown_entity",
                        f"unknown variant: {variant_id}",
                        context={"variant_id": variant_id},
                    )
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": {"variant": variant_view(row)},
                    }
                )
                continue
            if name == "compare_variants":
                try:
                    comparison = await compare_variants(
                        session,
                        job.project_id,
                        parsed.left_variant_id,  # type: ignore[attr-defined]
                        parsed.right_variant_id,  # type: ignore[attr-defined]
                    )
                except VariantError as exc:
                    raise AgentToolError(
                        "unknown_entity",
                        exc.detail,
                        context={"code": exc.code},
                    ) from exc
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": {"comparison": comparison},
                    }
                )
                continue
            if name == "get_variant_budget":
                try:
                    report = await budget_report(
                        session,
                        job.project_id,
                        parsed.variant_id,  # type: ignore[attr-defined]
                    )
                except BudgetError as exc:
                    raise AgentToolError(
                        "unknown_entity",
                        exc.detail,
                        context={"code": exc.code},
                    ) from exc
                tool_results.append(
                    {
                        "index": index,
                        "name": name,
                        "result": {"budget": report},
                    }
                )
                continue
            tool_results.append(
                {
                    "index": index,
                    "name": name,
                    "result": execute_read_tool(validation_scene, raw_call),
                }
            )
            continue

        if name in MUTATION_TOOL_NAMES:
            command = tool_call_to_command(
                raw_call,
                base_revision_id=current.id,
                request_text=request_text,
            )
            validation_scene = apply_command(validation_scene, command)
            commands.append(command)
            affected_entity_ids.append(command.target_id)
            referenced_asset_ids.extend(command.reference_asset_ids)
            command_summary.append(
                {
                    "operation": command.operation.value,
                    "target_id": command.target_id,
                    "parameters": command.parameters,
                    "reference_asset_ids": command.reference_asset_ids,
                }
            )
            continue

        if name in CONTROL_TOOL_NAMES:
            if name == "create_design_revision":
                revision_marker_calls.append(index)
                continue
            if name == "render_preview":
                preview_requests.append(
                    {
                        "index": index,
                        "camera_id": parsed.model_dump(exclude_none=True).get("camera_id"),
                    }
                )
                continue

        raise ValueError(f"unsupported agent tool: {name}")

    # Persist mutations only after the complete proposal has validated in memory.
    applied_revision_ids: list[str] = []
    actual_base = current.id
    for command in commands:
        command = command.model_copy(update={"base_revision_id": actual_base})
        revision = await apply_scene_command(
            session,
            job.project_id,
            command,
            model_profile=job.payload.get("model_profile"),
            correlation_id=job.correlation_id,
        )
        actual_base = revision.id
        applied_revision_ids.append(revision.id)

    for index in revision_marker_calls:
        tool_results.append(
            {
                "index": index,
                "name": "create_design_revision",
                "result": {"revision_id": actual_base},
            }
        )

    generation_job_ids: list[str] = []
    if commands and validation_scene.cameras:
        protected_entity_ids = [
            entity.id
            for entity in validation_scene.entities
            if entity.locks.geometry or entity.locks.transform
        ]
        generation_job = await queue_design_generation(
            session,
            project_id=job.project_id,
            design_revision_id=actual_base,
            camera_id=validation_scene.cameras[0].id,
            request_text=request_text or "",
            affected_entity_ids=affected_entity_ids,
            protected_entity_ids=protected_entity_ids,
            reference_asset_ids=referenced_asset_ids,
            correlation_id=job.correlation_id,
            dispatcher=dispatcher,
        )
        generation_job_ids.append(generation_job.id)

    preview_job_ids: list[str] = []
    for preview in preview_requests:
        requested_camera_id = preview["camera_id"]
        if requested_camera_id is not None:
            camera = next(
                (
                    item
                    for item in validation_scene.cameras
                    if item.id == requested_camera_id
                ),
                None,
            )
            if camera is None:
                raise ValueError(f"unknown camera: {requested_camera_id}")
        else:
            camera = validation_scene.cameras[0] if validation_scene.cameras else None
        if camera is None:
            raise ValueError("render_preview requires at least one canonical camera")

        render_id = str(uuid4())
        preview_job = await create_job(
            session,
            project_id=job.project_id,
            job_type="render.blender",
            required_capabilities=["blender_render"],
            idempotency_key=(
                f"agent-preview:{job.id}:{camera.id}:{actual_base}"
            ),
            correlation_id=job.correlation_id,
            payload={
                "purpose": "agent_preview",
                "render_id": render_id,
                "scene_revision_id": actual_base,
                "camera_id": camera.id,
                "scene": validation_scene.model_dump(mode="json", exclude_none=True),
                "renderer_profile": "blender-cycles-v0",
                "requested_by_job_id": job.id,
            },
            dispatcher=dispatcher,
        )
        preview_job_ids.append(preview_job.id)
        tool_results.append(
            {
                "index": preview["index"],
                "name": "render_preview",
                "result": {"job_id": preview_job.id},
            }
        )

    enriched = dict(result)
    enriched["tool_results"] = sorted(tool_results, key=lambda item: item["index"])
    enriched["commands"] = command_summary
    enriched["affected_entity_ids"] = sorted(set(affected_entity_ids))
    enriched["applied_revision_ids"] = applied_revision_ids
    enriched["final_revision_id"] = actual_base
    enriched["generation_job_ids"] = generation_job_ids
    enriched["preview_job_ids"] = preview_job_ids
    return enriched
