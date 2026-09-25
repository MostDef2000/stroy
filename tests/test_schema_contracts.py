import json
from pathlib import Path

import jsonschema
import pytest

from stroy.domain.models import DesignCommand, EntityLocks, Scene, SceneEntity


ROOT = Path(__file__).resolve().parents[1]


def schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def test_scene_model_matches_versioned_json_schema() -> None:
    scene = Scene(
        scene_id="scene.living",
        project_id="project-1",
        entities=[
            SceneEntity(
                id="surface.wall.living.north",
                kind="wall",
                locks=EntityLocks(geometry=True, transform=True),
                geometry={"dimensions_mm": [5000, 120, 2800]},
            )
        ],
        cameras=[],
    )
    jsonschema.validate(scene.model_dump(mode="json", exclude_none=True), schema("scene.schema.json"))


def test_design_command_matches_versioned_json_schema() -> None:
    command = DesignCommand(
        command_id="command-1",
        base_revision_id="revision-1",
        operation="set_material",
        target_id="surface.wall.living.north",
        parameters={"material_ref": "material.paint.warm-white"},
        origin="user",
    )
    jsonschema.validate(
        command.model_dump(mode="json", exclude_none=True),
        schema("design-command.schema.json"),
    )


def test_duplicate_scene_ids_are_rejected_before_persistence() -> None:
    with pytest.raises(ValueError, match="IDs must be unique"):
        Scene(
            scene_id="scene.invalid",
            project_id="project-1",
            entities=[
                SceneEntity(id="object.sofa.main", kind="furniture"),
                SceneEntity(id="object.sofa.main", kind="furniture"),
            ],
        )
