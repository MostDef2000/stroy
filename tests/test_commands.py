import pytest

from stroy.domain import (
    DesignCommand,
    EntityIntent,
    EntityLocks,
    Scene,
    SceneEntity,
    apply_command,
)
from stroy.domain.commands import CommandRejected
from stroy.domain.models import EntityState


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


# ---------------------------------------------------------------------------
# R2 intent/lock enforcement matrix (#178)
# ---------------------------------------------------------------------------


def _scene_with(
    *,
    intent=None,
    existence=False,
    geometry=False,
    transform=False,
    material=False,
) -> Scene:
    source = scene()
    sofa = next(entity for entity in source.entities if entity.id == "object.sofa.main")
    sofa.intent = intent
    sofa.locks = EntityLocks(
        geometry=geometry, transform=transform, material=material, existence=existence
    )
    return source


def test_keep_intent_blocks_remove_and_replace() -> None:
    for operation, references in (
        ("remove_object", []),
        ("replace_object_from_reference", ["asset-reference"]),
    ):
        source = _intent_scene_for("keep")
        with pytest.raises(CommandRejected, match="keep"):
            apply_command(
                source,
                command(
                    operation,
                    "object.sofa.main",
                    parameters={},
                    reference_asset_ids=references,
                ),
            )
        # The keep-pinned entity is still there after the rejected command.
        assert any(entity.id == "object.sofa.main" for entity in source.entities)


def _intent_scene_for(intent: str) -> Scene:
    source = scene()
    sofa = next(entity for entity in source.entities if entity.id == "object.sofa.main")
    sofa.intent = EntityIntent(intent)
    return source


def test_remove_intent_allows_remove_but_blocks_replace() -> None:
    removed = apply_command(
        _intent_scene_for("remove"), command("remove_object", "object.sofa.main")
    )
    assert "object.sofa.main" not in {entity.id for entity in removed.entities}

    with pytest.raises(CommandRejected, match="marked for remove"):
        apply_command(
            _intent_scene_for("remove"),
            command(
                "replace_object_from_reference",
                "object.sofa.main",
                reference_asset_ids=["asset-reference"],
            ),
        )


def test_replace_intent_allows_replace() -> None:
    result = apply_command(
        _intent_scene_for("replace"),
        command(
            "replace_object_from_reference",
            "object.sofa.main",
            reference_asset_ids=["asset-reference"],
        ),
    )
    sofa = next(entity for entity in result.entities if entity.id == "object.sofa.main")
    assert sofa.metadata["replacement_reference_asset_id"] == "asset-reference"


def test_existence_lock_blocks_remove_and_replace() -> None:
    source = scene()
    source.entities[1].locks.existence = True
    with pytest.raises(CommandRejected, match="existence is locked"):
        apply_command(source, command("remove_object", "object.sofa.main"))
    with pytest.raises(CommandRejected, match="existence is locked"):
        apply_command(
            source,
            command(
                "replace_object_from_reference",
                "object.sofa.main",
                reference_asset_ids=["asset-reference"],
            ),
        )


def test_geometry_lock_still_blocks_move_remove_and_replace() -> None:
    source = scene()
    source.entities[1].locks.geometry = True
    with pytest.raises(CommandRejected, match="transform is locked"):
        apply_command(
            source,
            command("move_object", "object.sofa.main", parameters={"translation_mm": [1, 2, 3]}),
        )
    with pytest.raises(CommandRejected, match="geometry is locked"):
        apply_command(source, command("remove_object", "object.sofa.main"))
    with pytest.raises(CommandRejected, match="geometry is locked"):
        apply_command(
            source,
            command(
                "replace_object_from_reference",
                "object.sofa.main",
                reference_asset_ids=["asset-reference"],
            ),
        )


def test_transform_lock_blocks_move_but_not_remove() -> None:
    source = scene()
    source.entities[1].locks.transform = True
    with pytest.raises(CommandRejected, match="transform is locked"):
        apply_command(
            source,
            command("move_object", "object.sofa.main", parameters={"translation_mm": [1, 2, 3]}),
        )
    result = apply_command(source, command("remove_object", "object.sofa.main"))
    assert "object.sofa.main" not in {entity.id for entity in result.entities}


def test_material_lock_blocks_material_and_color() -> None:
    source = scene()
    source.entities[1].locks.material = True
    with pytest.raises(CommandRejected, match="material is locked"):
        apply_command(
            source,
            command("set_material", "object.sofa.main", parameters={"material_ref": "m.1"}),
        )
    with pytest.raises(CommandRejected, match="material is locked"):
        apply_command(
            source,
            command("set_color", "object.sofa.main", parameters={"color": "#fff"}),
        )


def test_set_intent_null_then_remove_succeeds() -> None:
    pinned = _intent_scene_for("keep")
    cleared = apply_command(
        pinned, command("set_intent", "object.sofa.main", parameters={"intent": None})
    )
    sofa = next(entity for entity in cleared.entities if entity.id == "object.sofa.main")
    assert sofa.intent is None

    removed = apply_command(cleared, command("remove_object", "object.sofa.main"))
    assert "object.sofa.main" not in {entity.id for entity in removed.entities}


def test_structure_entity_rejects_remove_and_replace_intent() -> None:
    source = scene()
    source.entities[1].state = EntityState.STRUCTURE
    for intent in ("remove", "replace"):
        with pytest.raises(CommandRejected, match="structure entity"):
            apply_command(
                source,
                command("set_intent", "object.sofa.main", parameters={"intent": intent}),
            )
    # keep and null are allowed on structure state.
    for intent in ("keep", None):
        result = apply_command(
            source,
            command("set_intent", "object.sofa.main", parameters={"intent": intent}),
        )
        target = next(
            entity for entity in result.entities if entity.id == "object.sofa.main"
        )
        assert target.intent == (EntityIntent.KEEP if intent else None)


def test_set_intent_unknown_value_rejected() -> None:
    with pytest.raises(CommandRejected, match="set_intent requires"):
        apply_command(
            scene(),
            command("set_intent", "object.sofa.main", parameters={"intent": "burn"}),
        )


def test_set_locks_merges_and_unknown_key_rejected() -> None:
    locked = apply_command(
        scene(),
        command(
            "set_locks",
            "object.sofa.main",
            parameters={"locks": {"existence": True, "transform": True}},
        ),
    )
    sofa = next(entity for entity in locked.entities if entity.id == "object.sofa.main")
    assert sofa.locks.existence is True
    assert sofa.locks.transform is True
    assert sofa.locks.geometry is False

    with pytest.raises(CommandRejected, match="unknown lock"):
        apply_command(
            scene(),
            command("set_locks", "object.sofa.main", parameters={"locks": {"pose": True}}),
        )
    with pytest.raises(CommandRejected, match="boolean"):
        apply_command(
            scene(),
            command("set_locks", "object.sofa.main", parameters={"locks": {"existence": "yes"}}),
        )


def test_set_state_structure_with_remove_intent_rejected() -> None:
    source = _intent_scene_for("remove")
    with pytest.raises(CommandRejected, match="marked for remove"):
        apply_command(
            source,
            command("set_state", "object.sofa.main", parameters={"state": "structure"}),
        )


def test_set_state_design_allowed_with_remove_intent() -> None:
    source = _intent_scene_for("remove")
    result = apply_command(
        source, command("set_state", "object.sofa.main", parameters={"state": "design"})
    )
    sofa = next(entity for entity in result.entities if entity.id == "object.sofa.main")
    assert sofa.state is EntityState.DESIGN
