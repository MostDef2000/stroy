import pytest

from stroy.agent import tool_call_to_command


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


def test_unknown_agent_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported agent tool"):
        tool_call_to_command(
            {"name": "write_database", "arguments": {"target_id": "anything"}},
            base_revision_id="revision-1",
        )
