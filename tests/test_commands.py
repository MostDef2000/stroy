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
            SceneEntity(
                id="object.sofa.main",
                kind="furniture",
            ),
            SceneEntity(
                id="object.coffee_table.main",
                kind="furniture",
            ),
            SceneEntity(
                id="light.ceiling.main",
                kind="light",
            ),
        ],
    )


def command(operation: str, target_id: str, **kwargs) -> DesignCommand:
    return DesignCommand(
        command_id=kwargs.pop("command_id", f"cmd-{operation}"),
        base_revision_id="rev-1",
        operation=operation,
        target_id=target_id,
        origin=kwargs.pop("origin", "user"),
        **kwargs,
    )


def test_material_edit_preserves_source_scene() -> None:
    source = scene()
    result = apply_command(
        source,
        command(
            "set_color",
            "object.sofa.main",
            parameters={"color": "#D7C4AB"},
        ),
    )
    assert source.entities[1].metadata == {}
    assert result.entities[1].metadata["color"] == "#D7C4AB"


def test_locked_geometry_cannot_move() -> None:
    with pytest.raises(CommandRejected, match="locked"):
        apply_command(
            scene(),
            command(
                "move_object",
                "surface.wall.living.north",
                parameters={"translation_mm": [1, 2, 3]},
                origin="agent",
            ),
        )


def test_locked_material_cannot_change() -> None:
    source = scene()
    source.entities[1].locks.material = True
    with pytest.raises(CommandRejected, match="material is locked"):
        apply_command(
            source,
            command(
                "set_material",
                "object.sofa.main",
                parameters={"material_ref": "material.fabric.beige"},
            ),
        )


@pytest.mark.parametrize(
    ("operation", "target", "parameters", "references", "assertion"),
    [
        (
            "set_material",
            "object.sofa.main",
            {"material_ref": "material.fabric.beige"},
            [],
            lambda result: result.entities[1].material_ref == "material.fabric.beige",
        ),
        (
            "move_object",
            "object.sofa.main",
            {"translation_mm": [100, 200, 300]},
            [],
            lambda result: result.entities[1].transform.translation_mm == (100, 200, 300),
        ),
        (
            "replace_object_from_reference",
            "object.sofa.main",
            {},
            ["asset-reference"],
            lambda result: (
                result.entities[1].metadata["replacement_reference_asset_id"]
                == "asset-reference"
            ),
        ),
        (
            "set_light_intent",
            "light.ceiling.main",
            {"intent": "warm ambient", "temperature_k": 3000},
            [],
            lambda result: result.entities[3].metadata["temperature_k"] == 3000,
        ),
    ],
)
def test_initial_edit_commands(
    operation,
    target,
    parameters,
    references,
    assertion,
) -> None:
    result = apply_command(
        scene(),
        command(
            operation,
            target,
            parameters=parameters,
            reference_asset_ids=references,
        ),
    )
    assert assertion(result)


def test_add_and_remove_object() -> None:
    added = apply_command(
        scene(),
        command(
            "add_object",
            "object.chair.accent",
            parameters={
                "entity": {
                    "id": "object.chair.accent",
                    "kind": "furniture",
                    "display_name": "Accent chair",
                }
            },
        ),
    )
    assert "object.chair.accent" in {entity.id for entity in added.entities}

    removed = apply_command(
        added,
        command("remove_object", "object.coffee_table.main"),
    )
    assert "object.coffee_table.main" not in {entity.id for entity in removed.entities}
