from __future__ import annotations

from typing import Any
from uuid import uuid4

from stroy.domain.models import DesignCommand


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_color",
            "description": "Change the color of an editable semantic scene entity.",
            "parameters": {
                "type": "object",
                "properties": {"target_id": {"type": "string"}, "color": {"type": "string"}},
                "required": ["target_id", "color"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_material",
            "description": "Assign a material reference to an editable entity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string"},
                    "material_ref": {"type": "string"},
                },
                "required": ["target_id", "material_ref"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_object",
            "description": "Remove an unlocked design object.",
            "parameters": {
                "type": "object",
                "properties": {"target_id": {"type": "string"}},
                "required": ["target_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_object",
            "description": "Move or rotate an unlocked object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string"},
                    "translation_mm": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "rotation_deg": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                },
                "required": ["target_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_object_from_reference",
            "description": "Replace an unlocked design object using an uploaded reference asset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string"},
                    "reference_asset_id": {"type": "string"},
                },
                "required": ["target_id", "reference_asset_id"],
                "additionalProperties": False,
            },
        },
    },
]


def tool_call_to_command(
    call: dict[str, Any],
    *,
    base_revision_id: str,
    request_text: str | None = None,
) -> DesignCommand:
    name = call.get("name")
    arguments = call.get("arguments") or {}
    supported = {item["function"]["name"] for item in TOOL_DEFINITIONS}
    if name not in supported:
        raise ValueError(f"unsupported agent tool: {name}")
    target_id = arguments.get("target_id")
    if not target_id:
        raise ValueError("agent tool requires target_id")

    parameters = dict(arguments)
    parameters.pop("target_id", None)
    references: list[str] = []
    if name == "replace_object_from_reference":
        reference = parameters.pop("reference_asset_id", None)
        if not reference:
            raise ValueError("replacement requires reference_asset_id")
        references.append(reference)

    return DesignCommand(
        command_id=str(uuid4()),
        base_revision_id=base_revision_id,
        operation=name,
        target_id=target_id,
        parameters=parameters,
        reference_asset_ids=references,
        origin="agent",
        request_text=request_text,
    )
