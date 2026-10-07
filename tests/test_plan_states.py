"""Plan-committed scene entities are tagged with the structure state layer."""

from __future__ import annotations

from stroy.domain.models import EntityState
from stroy.domain.plan import PlanDraft, PlanFloor, PlanOpening, PlanRoom, PlanScale, PlanWall
from stroy.services.plans import build_scene_from_draft


def one_room_draft() -> PlanDraft:
    return PlanDraft(
        scale=PlanScale(source="manual", mm_per_px=50.0),
        floors=[
            PlanFloor(
                name="main",
                level_mm=0.0,
                walls=[
                    PlanWall(
                        id="wall.1",
                        x1=0,
                        y1=0,
                        x2=4000,
                        y2=0,
                        thickness_mm=150.0,
                        openings=[
                            PlanOpening(
                                id="door.1",
                                kind="door",
                                t=0.5,
                                width_mm=900.0,
                                height_mm=2100.0,
                            )
                        ],
                    ),
                    PlanWall(id="wall.2", x1=4000, y1=0, x2=4000, y2=3000, thickness_mm=150.0),
                    PlanWall(id="wall.3", x1=4000, y1=3000, x2=0, y2=3000, thickness_mm=150.0),
                    PlanWall(id="wall.4", x1=0, y1=3000, x2=0, y2=0, thickness_mm=150.0),
                ],
                rooms=[
                    PlanRoom(
                        id="room.living",
                        name="Living room",
                        wall_ids=["wall.1", "wall.2", "wall.3", "wall.4"],
                    )
                ],
            )
        ],
    )


def test_plan_commit_tags_entities_structure() -> None:
    scene = build_scene_from_draft("project-1", one_room_draft())
    assert scene.entities, "draft must produce entities"
    for entity in scene.entities:
        assert entity.state is EntityState.STRUCTURE, entity.id

    kinds = {entity.kind.value for entity in scene.entities}
    assert {"room", "wall", "floor", "ceiling", "door"} <= kinds


def test_plan_commit_preserves_deterministic_ids() -> None:
    first = build_scene_from_draft("project-1", one_room_draft())
    second = build_scene_from_draft("project-1", one_room_draft())
    assert [entity.id for entity in first.entities] == [
        entity.id for entity in second.entities
    ]
    assert {entity.id for entity in first.entities} >= {"room.living", "wall.1", "door.1"}
