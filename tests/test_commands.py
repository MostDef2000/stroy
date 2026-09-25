import pytest

from stroy.domain import DesignCommand, EntityLocks, Scene, SceneEntity, apply_command
from stroy.domain.commands import CommandRejected


def scene() -> Scene:
    return Scene(
        scene_id="scene-1",
        project_id="project-1",
        entities=[
            SceneEntity(
                id="surface.wall.living.north",
                kind="wall",
                locks=EntityLocks(geometry=True, transform=True),
            ),
            SceneEntity(id="object.sofa.main", kind="furniture"),
            SceneEntity(id="object.coffee_table.main", kind="furniture"),
        ],
    )


def test_material_edit_preserves_source_scene() -> None:
    source = scene()
    command = DesignCommand(
        command_id="cmd-1",
        base_revision_id="rev-1",
        operation="set_color",
        target_id="object.sofa.main",
        parameters={"color": "#D7C4AB"},
        origin="user",
    )
    result = apply_command(source, command)
    assert source.entities[1].metadata == {}
    assert result.entities[1].metadata["color"] == "#D7C4AB"


def test_locked_geometry_cannot_move() -> None:
    command = DesignCommand(
        command_id="cmd-2",
        base_revision_id="rev-1",
        operation="move_object",
        target_id="surface.wall.living.north",
        parameters={"translation_mm": [1, 2, 3]},
        origin="agent",
    )
    with pytest.raises(CommandRejected):
        apply_command(scene(), command)


def test_remove_furniture() -> None:
    command = DesignCommand(
        command_id="cmd-3",
        base_revision_id="rev-1",
        operation="remove_object",
        target_id="object.coffee_table.main",
        origin="user",
    )
    result = apply_command(scene(), command)
    assert {entity.id for entity in result.entities} == {
        "surface.wall.living.north",
        "object.sofa.main",
    }
