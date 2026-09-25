from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from stroy.agent import tool_call_to_command
from stroy.db.models import JobRow
from stroy.domain.commands import CommandConflict, apply_command
from stroy.domain.models import Scene
from stroy.services.scenes import apply_scene_command, latest_revision


async def apply_design_agent_result(
    session: AsyncSession,
    job: JobRow,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Validate and apply design tool calls returned by a remote LLM worker.

    The LLM can only propose typed tools. Canonical scene mutation still happens
    on the control plane through the command engine and geometry locks.
    """
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

    # Validate the complete proposal against an in-memory copy before persisting
    # any command. This prevents a later invalid call from leaving a partial edit.
    validation_scene = Scene.model_validate(current.scene_json)
    commands = []
    validation_base = current.id
    for raw_call in calls:
        if not isinstance(raw_call, dict):
            raise ValueError("LLM tool call must be an object")
        command = tool_call_to_command(
            raw_call,
            base_revision_id=validation_base,
            request_text=request_text,
        )
        validation_scene = apply_command(validation_scene, command)
        commands.append(command)

    applied_revision_ids: list[str] = []
    actual_base = current.id
    for command in commands:
        command = command.model_copy(update={"base_revision_id": actual_base})
        revision = await apply_scene_command(session, job.project_id, command)
        actual_base = revision.id
        applied_revision_ids.append(revision.id)

    enriched = dict(result)
    enriched["applied_revision_ids"] = applied_revision_ids
    enriched["final_revision_id"] = actual_base
    return enriched
