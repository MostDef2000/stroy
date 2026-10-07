"""R1 state layers: EntityState on scene entities (#155)."""

from __future__ import annotations

import pytest

from stroy.domain import DesignCommand, EntityLocks, Scene, SceneEntity, apply_command
from stroy.domain.commands import CommandRejected
from stroy.domain.models import EntityState


def command(operation: str, target_id: str, **kwargs) -> DesignCommand:
    return DesignCommand(
        command_id=kwargs.pop("command_id", f"cmd-{operation}"),
        base_revision_id="rev-1",
        operation=operation,
        target_id=target_id,
        origin=kwargs.pop("origin", "user"),
        **kwargs,
    )


def scene() -> Scene:
    return Scene(
        scene_id="scene-1",
        project_id="project-1",
        entities=[
            SceneEntity(id="room.living", kind="room"),
            SceneEntity(id="object.sofa.main", kind="furniture", display_name="Sofa"),
        ],
    )


def test_entity_state_defaults_to_asis() -> None:
    entity = SceneEntity(id="object.sofa.main", kind="furniture")
    assert entity.state is EntityState.ASIS


def test_state_survives_scene_round_trip() -> None:
    source = Scene(
        scene_id="scene-1",
        project_id="project-1",
        entities=[
            SceneEntity(
                id="surface.wall.living.north",
                kind="wall",
                state=EntityState.STRUCTURE,
            ),
            SceneEntity(
                id="object.sofa.main",
                kind="furniture",
                state=EntityState.DESIGN,
            ),
            SceneEntity(id="object.table.main", kind="furniture"),
        ],
    )
    restored = Scene.model_validate(source.model_dump(mode="json", exclude_none=True))
    assert [entity.state for entity in restored.entities] == [
        EntityState.STRUCTURE,
        EntityState.DESIGN,
        EntityState.ASIS,
    ]


def test_set_state_applies_and_preserves_other_fields() -> None:
    result = apply_command(
        scene(),
        command(
            "set_state",
            "object.sofa.main",
            parameters={"state": "design"},
        ),
    )
    target = next(entity for entity in result.entities if entity.id == "object.sofa.main")
    assert target.state is EntityState.DESIGN
    assert target.display_name == "Sofa"
    assert target.kind.value == "furniture"
    # Source scene is untouched.
    assert next(
        entity for entity in scene().entities if entity.id == "object.sofa.main"
    ).state is EntityState.ASIS


def test_set_state_accepts_all_layers() -> None:
    for value in ("asis", "structure", "design"):
        result = apply_command(
            scene(),
            command("set_state", "room.living", parameters={"state": value}),
        )
        target = next(entity for entity in result.entities if entity.id == "room.living")
        assert target.state is EntityState(value)


def test_set_state_unknown_target_rejected() -> None:
    with pytest.raises(CommandRejected, match="unknown target entity"):
        apply_command(
            scene(),
            command("set_state", "object.missing", parameters={"state": "design"}),
        )


def test_set_state_unknown_value_rejected() -> None:
    with pytest.raises(CommandRejected, match="set_state requires"):
        apply_command(
            scene(),
            command("set_state", "object.sofa.main", parameters={"state": "demolished"}),
        )


def test_set_state_missing_value_rejected() -> None:
    with pytest.raises(CommandRejected, match="set_state requires"):
        apply_command(scene(), command("set_state", "object.sofa.main", parameters={}))


def test_set_state_not_blocked_by_locks() -> None:
    source = Scene(
        scene_id="scene-1",
        project_id="project-1",
        entities=[
            SceneEntity(
                id="surface.wall.living.north",
                kind="wall",
                locks=EntityLocks(geometry=True, transform=True, material=True),
            ),
        ],
    )
    result = apply_command(
        source,
        command("set_state", "surface.wall.living.north", parameters={"state": "structure"}),
    )
    assert result.entities[0].state is EntityState.STRUCTURE


def test_add_object_defaults_to_design_state() -> None:
    # Old clients predate state layers: their add_object payloads carry no
    # state, so the created entity must land on design (not the asis default).
    result = apply_command(
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
    added = next(entity for entity in result.entities if entity.id == "object.chair.accent")
    assert added.state is EntityState.DESIGN


def test_add_object_keeps_explicit_state() -> None:
    result = apply_command(
        scene(),
        command(
            "add_object",
            "surface.wall.new",
            parameters={
                "entity": {
                    "id": "surface.wall.new",
                    "kind": "wall",
                    "state": "asis",
                }
            },
        ),
    )
    added = next(entity for entity in result.entities if entity.id == "surface.wall.new")
    assert added.state is EntityState.ASIS
