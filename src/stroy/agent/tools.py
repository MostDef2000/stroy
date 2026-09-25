from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from uuid import uuid4

from stroy.domain.models import DesignCommand, Scene


class AgentToolError(ValueError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.context = context or {}

    def as_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "context": self.context,
        }


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetSceneArgs(ToolArgs):
    pass


class GetRoomArgs(ToolArgs):
    room_id: str = Field(min_length=1)


class GetEntityArgs(ToolArgs):
    target_id: str = Field(min_length=1)


class ListMaterialsArgs(ToolArgs):
    room_id: str | None = None


class SetMaterialArgs(ToolArgs):
    target_id: str = Field(min_length=1)
    material_ref: str = Field(min_length=1)


class SetColorArgs(ToolArgs):
    target_id: str = Field(min_length=1)
    color: str = Field(min_length=1)


class AddObjectArgs(ToolArgs):
    target_id: str = Field(min_length=1)
    entity: dict[str, Any]


class RemoveObjectArgs(ToolArgs):
    target_id: str = Field(min_length=1)


class ReplaceObjectFromReferenceArgs(ToolArgs):
    target_id: str = Field(min_length=1)
    reference_asset_id: str = Field(min_length=1)


class MoveObjectArgs(ToolArgs):
    target_id: str = Field(min_length=1)
    translation_mm: tuple[float, float, float] | None = None
    rotation_deg: tuple[float, float, float] | None = None
    scale: tuple[float, float, float] | None = None


class SetLightIntentArgs(ToolArgs):
    target_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    temperature_k: int | None = Field(default=None, ge=1000, le=20000)


class CreateDesignRevisionArgs(ToolArgs):
    label: str | None = Field(default=None, max_length=200)


class RenderPreviewArgs(ToolArgs):
    camera_id: str | None = None


_TOOL_SPECS: list[tuple[str, str, type[ToolArgs]]] = [
    ("get_scene", "Read the current project-scoped canonical scene.", GetSceneArgs),
    ("get_room", "Read one room and its project-scoped entities.", GetRoomArgs),
    ("get_entity", "Read one semantic entity by stable ID.", GetEntityArgs),
    (
        "list_materials",
        "List material references already present in the project scene.",
        ListMaterialsArgs,
    ),
    (
        "set_material",
        "Assign a material reference to an editable semantic scene entity.",
        SetMaterialArgs,
    ),
    ("set_color", "Change the color of an editable semantic scene entity.", SetColorArgs),
    ("add_object", "Add a typed design object to the canonical scene.", AddObjectArgs),
    ("remove_object", "Remove an unlocked design object.", RemoveObjectArgs),
    (
        "replace_object_from_reference",
        "Replace an unlocked design object using an uploaded reference asset.",
        ReplaceObjectFromReferenceArgs,
    ),
    ("move_object", "Move, rotate or scale an unlocked object.", MoveObjectArgs),
    (
        "set_light_intent",
        "Set semantic lighting intent for a light entity.",
        SetLightIntentArgs,
    ),
    (
        "create_design_revision",
        "Return the current design revision after accepted edits.",
        CreateDesignRevisionArgs,
    ),
    (
        "render_preview",
        "Request a deterministic preview render of the current scene revision.",
        RenderPreviewArgs,
    ),
]

TOOL_ARGUMENT_MODELS = {name: model for name, _, model in _TOOL_SPECS}
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }
    for name, description, model in _TOOL_SPECS
]

READ_TOOL_NAMES = {"get_scene", "get_room", "get_entity", "list_materials"}
MUTATION_TOOL_NAMES = {
    "set_material",
    "set_color",
    "add_object",
    "remove_object",
    "replace_object_from_reference",
    "move_object",
    "set_light_intent",
}
CONTROL_TOOL_NAMES = {"create_design_revision", "render_preview"}


def validate_tool_call(call: dict[str, Any]) -> tuple[str, ToolArgs]:
    name = call.get("name")
    if not isinstance(name, str) or name not in TOOL_ARGUMENT_MODELS:
        raise AgentToolError(
            "unknown_tool",
            f"unsupported agent tool: {name}",
            context={"tool_name": name},
        )
    arguments = call.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise AgentToolError(
            "invalid_tool_args",
            f"agent tool arguments must be an object: {name}",
            context={"tool_name": name},
        )
    try:
        parsed = TOOL_ARGUMENT_MODELS[name].model_validate(arguments)
    except ValueError as exc:
        raise AgentToolError(
            "invalid_tool_args",
            f"invalid arguments for agent tool {name}: {exc}",
            context={"tool_name": name},
        ) from exc
    return name, parsed


def execute_read_tool(scene: Scene, call: dict[str, Any]) -> dict[str, Any]:
    name, parsed = validate_tool_call(call)
    if name not in READ_TOOL_NAMES:
        raise AgentToolError(
            "invalid_tool_mode",
            f"tool is not read-only: {name}",
            context={"tool_name": name},
        )

    if name == "get_scene":
        return scene.model_dump(mode="json", exclude_none=True)

    if name == "get_room":
        room_id = parsed.room_id  # type: ignore[attr-defined]
        room = next(
            (
                entity
                for entity in scene.entities
                if entity.id == room_id and entity.kind.value == "room"
            ),
            None,
        )
        entities = [
            entity.model_dump(mode="json", exclude_none=True)
            for entity in scene.entities
            if entity.room_id == room_id
        ]
        if room is None and not entities:
            raise AgentToolError(
                "unknown_entity",
                f"unknown room: {room_id}",
                context={"entity_id": room_id},
            )
        return {
            "room": room.model_dump(mode="json", exclude_none=True) if room else None,
            "entities": entities,
        }

    if name == "get_entity":
        target_id = parsed.target_id  # type: ignore[attr-defined]
        entity = next((entity for entity in scene.entities if entity.id == target_id), None)
        if entity is None:
            raise AgentToolError(
                "unknown_entity",
                f"unknown entity: {target_id}",
                context={"entity_id": target_id},
            )
        return entity.model_dump(mode="json", exclude_none=True)

    room_id = parsed.room_id  # type: ignore[attr-defined]
    materials = sorted(
        {
            entity.material_ref
            for entity in scene.entities
            if entity.material_ref
            and (room_id is None or entity.room_id == room_id)
        }
    )
    return {"materials": materials}


def tool_call_to_command(
    call: dict[str, Any],
    *,
    base_revision_id: str,
    request_text: str | None = None,
) -> DesignCommand:
    name, parsed = validate_tool_call(call)
    if name not in MUTATION_TOOL_NAMES:
        raise AgentToolError(
            "invalid_tool_mode",
            f"agent tool does not map to a design command: {name}",
            context={"tool_name": name},
        )

    arguments = parsed.model_dump(exclude_none=True)
    target_id = arguments.pop("target_id")

    references: list[str] = []
    if name == "replace_object_from_reference":
        references.append(arguments.pop("reference_asset_id"))

    return DesignCommand(
        command_id=str(uuid4()),
        base_revision_id=base_revision_id,
        operation=name,
        target_id=target_id,
        parameters=arguments,
        reference_asset_ids=references,
        origin="agent",
        request_text=request_text,
    )
