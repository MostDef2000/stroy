import pytest

from stroy.agent import (
    TOOL_DEFINITIONS,
    execute_read_tool,
    tool_call_to_command,
    validate_tool_call,
)
from stroy.domain import EntityLocks, Scene, SceneEntity


def sample_scene() -> Scene:
    return Scene(
        scene_id="scene-1",
        project_id="project-1",
        entities=[
            SceneEntity(id="room.living", kind="room"),
            SceneEntity(
                id="surface.wall.living.north",
                kind="wall",
                room_id="room.living",
                material_ref="material.paint.white",
                locks=EntityLocks(geometry=True, transform=True),
            ),
            SceneEntity(
                id="object.sofa.main",
                kind="furniture",
                room_id="room.living",
                material_ref="material.fabric.gray",
            ),
        ],
    )


def test_tool_surface_contains_initial_contract() -> None:
    names = {item["function"]["name"] for item in TOOL_DEFINITIONS}
    assert names == {
        "get_scene",
        "get_room",
        "get_entity",
        "list_materials",
        "set_material",
        "set_color",
        "add_object",
        "remove_object",
        "replace_object_from_reference",
        "move_object",
        "set_light_intent",
        "create_design_revision",
        "render_preview",
    }
    for item in TOOL_DEFINITIONS:
        assert item["function"]["parameters"]["additionalProperties"] is False


def test_agent_tool_becomes_typed_command() -> None:
    command = tool_call_to_command(
        {
            "name": "set_color",
            "arguments": {
                "target_id": "object.sofa.main",
                "color": "#D7C4AB",
            },
        },
        base_revision_id="revision-1",
        request_text="сделай диван бежевым",
    )
    assert command.operation.value == "set_color"
    assert command.target_id == "object.sofa.main"
    assert command.parameters == {"color": "#D7C4AB"}
    assert command.origin.value == "agent"
    assert command.request_text == "сделай диван бежевым"


def test_reference_tool_separates_asset_from_parameters() -> None:
    command = tool_call_to_command(
        {
            "name": "replace_object_from_reference",
            "arguments": {
                "target_id": "object.sofa.main",
                "reference_asset_id": "asset-1",
            },
        },
        base_revision_id="revision-1",
    )
    assert command.reference_asset_ids == ["asset-1"]
    assert command.parameters == {}


def test_add_object_and_light_intent_convert_to_commands() -> None:
    added = tool_call_to_command(
        {
            "name": "add_object",
            "arguments": {
                "target_id": "object.chair.accent",
                "entity": {
                    "id": "object.chair.accent",
                    "kind": "furniture",
                },
            },
        },
        base_revision_id="revision-1",
    )
    assert added.operation.value == "add_object"
    assert added.parameters["entity"]["id"] == "object.chair.accent"

    lighting = tool_call_to_command(
        {
            "name": "set_light_intent",
            "arguments": {
                "target_id": "light.ceiling.main",
                "intent": "warm ambient",
                "temperature_k": 3000,
            },
        },
        base_revision_id="revision-1",
    )
    assert lighting.parameters == {
        "intent": "warm ambient",
        "temperature_k": 3000,
    }


def test_read_tools_are_project_scene_scoped() -> None:
    scene = sample_scene()
    entity = execute_read_tool(
        scene,
        {
            "name": "get_entity",
            "arguments": {"target_id": "object.sofa.main"},
        },
    )
    assert entity["id"] == "object.sofa.main"

    room = execute_read_tool(
        scene,
        {"name": "get_room", "arguments": {"room_id": "room.living"}},
    )
    assert {item["id"] for item in room["entities"]} == {
        "surface.wall.living.north",
        "object.sofa.main",
    }

    materials = execute_read_tool(
        scene,
        {"name": "list_materials", "arguments": {"room_id": "room.living"}},
    )
    assert materials["materials"] == [
        "material.fabric.gray",
        "material.paint.white",
    ]


def test_unknown_entity_read_fails_safely() -> None:
    with pytest.raises(ValueError, match="unknown entity"):
        execute_read_tool(
            sample_scene(),
            {"name": "get_entity", "arguments": {"target_id": "object.unknown"}},
        )


def test_unknown_or_malformed_agent_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported agent tool"):
        tool_call_to_command(
            {"name": "write_database", "arguments": {"target_id": "anything"}},
            base_revision_id="revision-1",
        )

    with pytest.raises(ValueError, match="invalid arguments"):
        validate_tool_call(
            {
                "name": "set_color",
                "arguments": {
                    "target_id": "object.sofa.main",
                    "color": "#fff",
                    "unexpected": "blocked",
                },
            }
        )


def test_read_tool_cannot_be_converted_to_mutation() -> None:
    with pytest.raises(ValueError, match="does not map"):
        tool_call_to_command(
            {"name": "get_scene", "arguments": {}},
            base_revision_id="revision-1",
        )
