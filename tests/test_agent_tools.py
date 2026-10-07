import pytest

from stroy.agent import (
    TOOL_DEFINITIONS,
    execute_read_tool,
    tool_call_to_command,
    validate_tool_call,
)
from stroy.domain import EntityIntent, EntityLocks, Scene, SceneEntity


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
        "get_design_check",
        "set_material",
        "set_color",
        "set_state",
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


def test_design_check_tool_is_read_only_and_args_are_typed() -> None:
    from stroy.agent import READ_TOOL_NAMES, MUTATION_TOOL_NAMES

    assert "get_design_check" in READ_TOOL_NAMES
    # R2: intent/locks are owner/UI-only commands — never agent mutations.
    assert "set_intent" not in MUTATION_TOOL_NAMES
    assert "set_locks" not in MUTATION_TOOL_NAMES
    with pytest.raises(ValueError, match="unsupported agent tool"):
        validate_tool_call(
            {
                "name": "set_intent",
                "arguments": {"target_id": "object.sofa.main", "intent": "keep"},
            }
        )
    with pytest.raises(ValueError, match="unsupported agent tool"):
        validate_tool_call(
            {
                "name": "set_locks",
                "arguments": {
                    "target_id": "object.sofa.main",
                    "locks": {"existence": True},
                },
            }
        )


def test_design_check_tool_rejects_non_positive_walkway() -> None:
    with pytest.raises(ValueError, match="invalid arguments"):
        validate_tool_call(
            {
                "name": "get_design_check",
                "arguments": {"min_walkway_mm": 0},
            }
        )


def test_scene_read_includes_intent_and_locks() -> None:
    scene = sample_scene()
    sofa = scene.entities[2]
    sofa.intent = EntityIntent.KEEP
    sofa.locks.existence = True

    scene_payload = execute_read_tool(scene, {"name": "get_scene", "arguments": {}})
    sofa_view = next(
        item for item in scene_payload["entities"] if item["id"] == "object.sofa.main"
    )
    assert sofa_view["intent"] == "keep"
    assert sofa_view["locks"]["existence"] is True

    entity_view = execute_read_tool(
        scene,
        {"name": "get_entity", "arguments": {"target_id": "object.sofa.main"}},
    )
    assert entity_view["intent"] == "keep"
    assert entity_view["locks"] == {
        "geometry": False,
        "transform": False,
        "material": False,
        "existence": True,
    }


def test_design_check_cannot_run_scene_only_or_as_mutation() -> None:
    # The tool needs the database (persisted report or compute): scene-only
    # execution must fail cleanly instead of falling through to other reads.
    with pytest.raises(ValueError, match="service execution"):
        execute_read_tool(
            sample_scene(),
            {"name": "get_design_check", "arguments": {}},
        )
    with pytest.raises(ValueError, match="does not map"):
        tool_call_to_command(
            {"name": "get_design_check", "arguments": {}},
            base_revision_id="revision-1",
        )


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


async def test_get_design_check_persists_then_reuses_report(tmp_path) -> None:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from stroy.db.base import Base
    from stroy.db.models import ProjectRow, SceneRevisionRow, ValidationReportRow
    from stroy.services.validation import execute_design_check_tool

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'check.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            project = ProjectRow(name="Flat")
            session.add(project)
            await session.commit()
            revision = SceneRevisionRow(
                project_id=project.id,
                content_hash="hash-1",
                scene_json=sample_scene().model_dump(mode="json", exclude_none=True),
            )
            session.add(revision)
            await session.commit()

            first = await execute_design_check_tool(
                session, project.id, scene_revision_id=revision.id
            )
            assert first["report"]["schema_version"] == "0.1.0"
            assert first["report"]["scene_revision_id"] == revision.id
            assert first["scene_revision_id"] == revision.id
            assert len(first["report_hash"]) == 64
            assert first["report"]["results"] == []

            counted = await session.execute(
                select(func.count()).select_from(ValidationReportRow)
            )
            assert counted.scalar_one() == 1

            # Second call reuses the persisted report instead of recomputing.
            second = await execute_design_check_tool(
                session, project.id, scene_revision_id=revision.id
            )
            assert second["id"] == first["id"]
            counted = await session.execute(
                select(func.count()).select_from(ValidationReportRow)
            )
            assert counted.scalar_one() == 1
    finally:
        await engine.dispose()
