"""R2 spatial validator rules: per-rule hit/pass, determinism, geometry."""

from __future__ import annotations

import math

import pytest

from stroy.domain.models import Scene, SceneEntity, Transform
from stroy.spatial import (
    ValidationConfig,
    validate_scene,
)
from stroy.spatial.validator import (
    RULE_BED_ACCESS,
    RULE_CHAIR_PULLOUT,
    RULE_CABINET_OPENING,
    RULE_DOOR_SWING,
    RULE_DOOR_SWING_UNCHECKED,
    RULE_OBJECT_COLLISION,
    RULE_OPENING_BLOCKED,
    RULE_OUTSIDE_ROOM,
    RULE_SOFA_ACCESS,
    RULE_WALKWAY_MIN,
    RULE_WALL_COLLISION,
    report_hash,
)


def base_scene(entities: list[SceneEntity]) -> Scene:
    return Scene(scene_id="scene-1", project_id="project-1", entities=entities)


def wall(entity_id: str, x1: float, y1: float, x2: float, y2: float, thickness: float = 120.0) -> SceneEntity:
    length = math.hypot(x2 - x1, y2 - y1)
    return SceneEntity(
        id=entity_id,
        kind="wall",
        geometry={
            "a": [x1, y1],
            "b": [x2, y2],
            "dimensions_mm": {"length": length, "thickness": thickness, "height": 2700},
        },
    )


def floor(entity_id: str, room_id: str, bbox: tuple[float, float, float, float]) -> SceneEntity:
    return SceneEntity(
        id=entity_id,
        kind="floor",
        room_id=room_id,
        geometry={
            "dimensions_mm": [bbox[2] - bbox[0], bbox[3] - bbox[1], 0.0],
            "bbox": list(bbox),
        },
    )


def obj(
    entity_id: str,
    x: float,
    y: float,
    width: float,
    depth: float,
    *,
    rotation: float = 0.0,
    room_id: str | None = None,
    metadata: dict | None = None,
) -> SceneEntity:
    return SceneEntity(
        id=entity_id,
        kind="furniture",
        room_id=room_id,
        metadata=metadata or {},
        transform=Transform(translation_mm=(x, y, 0), rotation_deg=(0, 0, rotation)),
        geometry={"dimensions_mm": [width, depth, 800]},
    )


def opening(
    entity_id: str,
    host_wall_id: str,
    t: float,
    width_mm: float,
    *,
    kind: str = "door",
    metadata: dict | None = None,
) -> SceneEntity:
    opening_kind = {"door": "door", "window": "window", "arch": "arch"}.get(
        kind, "door"
    )
    return SceneEntity(
        id=entity_id,
        kind=opening_kind,
        geometry={
            "host_wall_id": host_wall_id,
            "t": t,
            "width_mm": width_mm,
            "height_mm": 2100,
            "sill_mm": 0,
        },
        metadata={"opening_kind": opening_kind, **(metadata or {})},
    )


def results_for(report, rule_id: str):
    return [item for item in report.results if item.rule_id == rule_id]


# -- object.object_collision -------------------------------------------------


def test_object_collision_hit_and_pass() -> None:
    colliding = base_scene(
        [
            obj("object.a.main", 1000, 1000, 600, 600),
            obj("object.b.main", 1100, 1000, 600, 600),
        ]
    )
    report = validate_scene(colliding, scene_revision_id="rev-1")
    hits = results_for(report, RULE_OBJECT_COLLISION)
    assert len(hits) == 1
    assert hits[0].severity.value == "error"
    assert hits[0].entity_ids == ["object.a.main", "object.b.main"]
    assert hits[0].measured_mm is None

    separated = base_scene(
        [
            obj("object.a.main", 1000, 1000, 600, 600),
            obj("object.b.main", 2000, 1000, 600, 600),
        ]
    )
    report = validate_scene(separated, scene_revision_id="rev-1")
    assert results_for(report, RULE_OBJECT_COLLISION) == []


# -- object.wall_collision ---------------------------------------------------


def test_object_wall_collision_hit_and_pass() -> None:
    overlapping = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            obj("object.sofa.main", 1000, 100, 2000, 900),
        ]
    )
    report = validate_scene(overlapping, scene_revision_id="rev-1")
    hits = results_for(report, RULE_WALL_COLLISION)
    assert len(hits) == 1
    assert hits[0].entity_ids == ["object.sofa.main", "wall.south"]
    assert hits[0].severity.value == "error"

    flush = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            # Flush against the wall face (wall rect spans y in [-60, 60]).
            obj("object.sofa.main", 1000, 710, 2000, 1300),
        ]
    )
    report = validate_scene(flush, scene_revision_id="rev-1")
    assert results_for(report, RULE_WALL_COLLISION) == []


# -- object.outside_room_bounds ----------------------------------------------


def test_outside_room_bounds_measured_and_skipped_without_floor() -> None:
    outside = base_scene(
        [
            floor("floor.room.living", "room.living", (0, 0, 5000, 4000)),
            obj("object.sofa.main", 4900, 2000, 1000, 600, room_id="room.living"),
        ]
    )
    report = validate_scene(outside, scene_revision_id="rev-1")
    hits = results_for(report, RULE_OUTSIDE_ROOM)
    assert len(hits) == 1
    assert hits[0].measured_mm == 400.0  # 4900 + 500 = 5400 > 5000
    assert hits[0].severity.value == "error"

    inside = base_scene(
        [
            floor("floor.room.living", "room.living", (0, 0, 5000, 4000)),
            obj("object.sofa.main", 2000, 2000, 1000, 600, room_id="room.living"),
        ]
    )
    report = validate_scene(inside, scene_revision_id="rev-1")
    assert results_for(report, RULE_OUTSIDE_ROOM) == []

    # No floor bbox for the room -> the rule is skipped entirely.
    no_floor = base_scene(
        [obj("object.sofa.main", 90000, 2000, 1000, 600, room_id="room.living")]
    )
    report = validate_scene(no_floor, scene_revision_id="rev-1")
    assert results_for(report, RULE_OUTSIDE_ROOM) == []


# -- opening.blocked ----------------------------------------------------------


def test_opening_blocked_t_position_and_door_coverage() -> None:
    # Wall 0..4000 on the x axis; door at t=0.25 sits centered at x=1000.
    scene = base_scene(
        [
            wall("wall.south", 0, 0, 4000, 0),
            opening("door.main", "wall.south", 0.25, 900),
            # 500 mm of the 900 mm passage covered -> 56% -> warning.
            obj("object.a.main", 1000, 30, 500, 500),
        ]
    )
    report = validate_scene(scene, scene_revision_id="rev-1")
    hits = results_for(report, RULE_OPENING_BLOCKED)
    assert len(hits) == 1
    assert hits[0].severity.value == "warning"
    assert hits[0].entity_ids == ["door.main", "object.a.main"]
    assert hits[0].measured_mm == 500.0

    # Wider coverage: 700/900 = 78% >= 60% -> error for a door.
    # (t=0.25 on a 5000-long wall centers the opening at x=1250.)
    blocking = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            opening("door.main", "wall.south", 0.25, 900),
            obj("object.a.main", 1250, 30, 700, 500),
        ]
    )
    report = validate_scene(blocking, scene_revision_id="rev-1")
    hits = results_for(report, RULE_OPENING_BLOCKED)
    assert len(hits) == 1
    assert hits[0].severity.value == "error"
    assert hits[0].measured_mm == 700.0

    # An object at the far end of the wall (t=0.75 region) does not block
    # the t=0.25 door: the opening position comes from the host-wall param.
    far = base_scene(
        [
            wall("wall.south", 0, 0, 4000, 0),
            opening("door.main", "wall.south", 0.25, 900),
            obj("object.a.main", 3000, 30, 500, 500),
        ]
    )
    report = validate_scene(far, scene_revision_id="rev-1")
    assert results_for(report, RULE_OPENING_BLOCKED) == []


# -- clearance.walkway_min ----------------------------------------------------


def test_walkway_min_per_object_results_and_measured_gap() -> None:
    scene = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            obj("object.a.main", 1000, 1000, 600, 600),
            obj("object.b.main", 2000, 1000, 600, 600),
        ]
    )
    report = validate_scene(
        scene, scene_revision_id="rev-1", config=ValidationConfig(min_walkway_mm=600)
    )
    hits = results_for(report, RULE_WALKWAY_MIN)
    # One result per object below threshold (two objects -> two results).
    assert len(hits) == 2
    assert {item.measured_mm for item in hits} == {400.0}
    for item in hits:
        assert item.entity_ids == ["object.a.main", "object.b.main"]
        assert item.expected_min_mm == 600.0

    roomy = base_scene(
        [
            obj("object.a.main", 1000, 1000, 600, 600),
            obj("object.b.main", 2400, 1000, 600, 600),
        ]
    )
    report = validate_scene(
        roomy, scene_revision_id="rev-1", config=ValidationConfig(min_walkway_mm=600)
    )
    assert results_for(report, RULE_WALKWAY_MIN) == []


def test_walkway_object_to_wall_measured() -> None:
    scene = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            # Footprint y in [250, 950]; wall rect top edge at y=60 -> gap 190.
            obj("object.sofa.main", 1000, 600, 2000, 700),
        ]
    )
    report = validate_scene(scene, scene_revision_id="rev-1")
    hits = results_for(report, RULE_WALKWAY_MIN)
    assert len(hits) == 1
    assert hits[0].entity_ids == ["object.sofa.main", "wall.south"]
    assert hits[0].measured_mm == 190.0


# -- envelope.door_swing ------------------------------------------------------


def test_door_swing_unchecked_without_metadata() -> None:
    scene = base_scene(
        [
            wall("wall.south", 0, 0, 4000, 0),
            opening("door.main", "wall.south", 0.25, 900),
        ]
    )
    report = validate_scene(scene, scene_revision_id="rev-1")
    unchecked = results_for(report, RULE_DOOR_SWING_UNCHECKED)
    assert len(unchecked) == 1
    assert unchecked[0].severity.value == "info"
    assert unchecked[0].entity_ids == ["door.main"]
    assert results_for(report, RULE_DOOR_SWING) == []


def test_door_swing_blocked_arc() -> None:
    scene = base_scene(
        [
            wall("wall.south", 0, 0, 4000, 0),
            opening(
                "door.main",
                "wall.south",
                0.25,
                900,
                metadata={"hinge": "start"},
            ),
            # Inside the 90-degree arc (hinge at (550,0), radius 900).
            obj("object.a.main", 800, 400, 300, 300),
        ]
    )
    report = validate_scene(scene, scene_revision_id="rev-1")
    hits = results_for(report, RULE_DOOR_SWING)
    assert len(hits) == 1
    assert hits[0].severity.value == "warning"
    assert hits[0].entity_ids == ["door.main", "object.a.main"]
    assert results_for(report, RULE_DOOR_SWING_UNCHECKED) == []

    # Outside the arc: no swing result and no unchecked info.
    clear = base_scene(
        [
            wall("wall.south", 0, 0, 4000, 0),
            opening(
                "door.main",
                "wall.south",
                0.25,
                900,
                metadata={"hinge": "start"},
            ),
            obj("object.a.main", 3000, 3000, 300, 300),
        ]
    )
    report = validate_scene(clear, scene_revision_id="rev-1")
    assert results_for(report, RULE_DOOR_SWING) == []
    assert results_for(report, RULE_DOOR_SWING_UNCHECKED) == []


# -- envelope furniture clearances --------------------------------------------


def test_chair_pullout_and_sofa_access_front_clearance() -> None:
    # Unobstructed: no clearance results at all (rules warn only on deficit).
    clear = base_scene(
        [
            obj("object.chair.accent", 2000, 2000, 600, 600),
            obj("object.sofa.main", 4000, 2000, 2000, 900),
        ]
    )
    report = validate_scene(clear, scene_revision_id="rev-1")
    assert results_for(report, RULE_CHAIR_PULLOUT) == []
    assert results_for(report, RULE_SOFA_ACCESS) == []

    # A table in front of the sofa leaves ~150 mm of the 600 mm access.
    blocked = base_scene(
        [
            obj("object.chair.accent", 2000, 2000, 600, 600),
            obj("object.sofa.main", 4000, 2000, 2000, 900),
            obj("object.table.coffee", 4000, 1200, 600, 400),
        ]
    )
    report = validate_scene(blocked, scene_revision_id="rev-1")
    chair = results_for(report, RULE_CHAIR_PULLOUT)
    assert chair == []  # chair strip stays clear
    sofa = results_for(report, RULE_SOFA_ACCESS)
    assert len(sofa) == 1
    assert sofa[0].severity.value == "warning"
    assert sofa[0].entity_ids == ["object.sofa.main", "object.table.coffee"]
    # Sofa front edge y=1550, table top edge y=1400 -> 150 mm clear.
    assert sofa[0].measured_mm == pytest.approx(150.0, abs=1.5)
    assert sofa[0].expected_min_mm == 600.0


def test_chair_pullout_blocked_by_wall() -> None:
    # Chair back (-Y side) 140 mm from the wall face: only ~140 mm clear
    # of the 750 mm pullout depth (bisection precision is 1 mm).
    scene = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            # Footprint y in [200, 800]; wall band top edge at y=60.
            obj("object.chair.accent", 2000, 500, 600, 600),
        ]
    )
    report = validate_scene(scene, scene_revision_id="rev-1")
    hits = results_for(report, RULE_CHAIR_PULLOUT)
    assert len(hits) == 1
    assert hits[0].measured_mm == pytest.approx(140.0, abs=1.5)
    assert hits[0].expected_min_mm == 750.0


def test_bed_access_long_side_logic() -> None:
    # Bed 2000x1600: long sides are the +/-v edges. One side blocked by a
    # wall, the other open -> pass. Both sides blocked -> warning with the
    # best-side measurement.
    one_side_open = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            obj("object.bed.main", 2000, 2000, 2000, 1600),
        ]
    )
    report = validate_scene(one_side_open, scene_revision_id="rev-1")
    assert results_for(report, RULE_BED_ACCESS) == []

    both_blocked = base_scene(
        [
            wall("wall.south", 0, 1000, 5000, 1000),
            wall("wall.north", 0, 3000, 5000, 3000),
            obj("object.bed.main", 2000, 2000, 2000, 1600),
        ]
    )
    report = validate_scene(both_blocked, scene_revision_id="rev-1")
    hits = results_for(report, RULE_BED_ACCESS)
    assert len(hits) == 1
    assert hits[0].severity.value == "warning"
    assert hits[0].entity_ids == ["object.bed.main"]
    # Both sides leave the same 140 mm gap to the blocking walls
    # (bisection precision is 1 mm).
    assert hits[0].measured_mm == pytest.approx(140.0, abs=1.5)


def test_cabinet_opening_only_with_metadata() -> None:
    with_depth = base_scene(
        [
            obj(
                "object.cabinet.main",
                2000,
                200,
                1200,
                600,
                metadata={"opening_depth_mm": 500},
            )
        ]
    )
    report = validate_scene(with_depth, scene_revision_id="rev-1")
    hits = results_for(report, RULE_CABINET_OPENING)
    assert len(hits) == 0  # unobstructed: full depth available

    blocked = base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            # Flush against the wall face: footprint y in [60, 660], so the
            # front (-Y) clearance strip starts inside the wall band.
            obj(
                "object.cabinet.main",
                2000,
                360,
                1200,
                600,
                metadata={"opening_depth_mm": 500},
            ),
        ]
    )
    report = validate_scene(blocked, scene_revision_id="rev-1")
    hits = results_for(report, RULE_CABINET_OPENING)
    assert len(hits) == 1
    # The strip starts inside the wall band -> blocked at any depth.
    assert hits[0].measured_mm == pytest.approx(0.0, abs=1.5)
    assert hits[0].expected_min_mm == 500.0

    without_metadata = base_scene([obj("object.cabinet.main", 2000, 360, 1200, 600)])
    report = validate_scene(without_metadata, scene_revision_id="rev-1")
    assert results_for(report, RULE_CABINET_OPENING) == []


# -- rotation: SAT vs AABB ----------------------------------------------------


def test_rotated_sofa_sat_not_aabb() -> None:
    # A 1000x1000 square and a 1600x300 slab rotated 45 degrees centered at
    # (1100, 1100): their AABBs overlap, the rotated footprints do not.
    scene = base_scene(
        [
            obj("object.a.main", 0, 0, 1000, 1000),
            obj("object.b.main", 1100, 1100, 1600, 300, rotation=45),
        ]
    )
    report = validate_scene(scene, scene_revision_id="rev-1")
    assert results_for(report, RULE_OBJECT_COLLISION) == []

    # Moving the slab 200 mm closer makes the actual footprints overlap.
    colliding = base_scene(
        [
            obj("object.a.main", 0, 0, 1000, 1000),
            obj("object.b.main", 900, 900, 1600, 300, rotation=45),
        ]
    )
    report = validate_scene(colliding, scene_revision_id="rev-1")
    hits = results_for(report, RULE_OBJECT_COLLISION)
    assert len(hits) == 1
    assert hits[0].entity_ids == ["object.a.main", "object.b.main"]


# -- determinism --------------------------------------------------------------


def _validation_scene() -> Scene:
    return base_scene(
        [
            wall("wall.south", 0, 0, 5000, 0),
            floor("floor.room.living", "room.living", (0, 0, 5000, 4000)),
            opening("door.main", "wall.south", 0.25, 900),
            obj("object.sofa.main", 1000, 1000, 2000, 900, room_id="room.living"),
            obj("object.chair.accent", 3000, 2000, 600, 600, room_id="room.living"),
            obj("object.bed.main", 2000, 3600, 2000, 1600, room_id="room.living"),
        ]
    )


def test_determinism_byte_identical_two_runs() -> None:
    scene = _validation_scene()
    config = ValidationConfig(min_walkway_mm=600)
    first = validate_scene(scene, scene_revision_id="rev-1", config=config)
    second = validate_scene(scene, scene_revision_id="rev-1", config=config)
    assert first.model_dump_json() == second.model_dump_json()
    assert report_hash(first.model_dump(mode="json")) == report_hash(
        second.model_dump(mode="json")
    )


def test_stable_sort_despite_entity_insertion_order() -> None:
    config = ValidationConfig(min_walkway_mm=600)
    report = validate_scene(
        _validation_scene(), scene_revision_id="rev-1", config=config
    )
    reversed_scene = Scene(
        scene_id="scene-1",
        project_id="project-1",
        entities=list(reversed(_validation_scene().entities)),
    )
    reordered = validate_scene(
        reversed_scene, scene_revision_id="rev-1", config=config
    )
    keys_a = [
        (
            item.severity.value,
            item.rule_id,
            tuple(item.entity_ids),
            item.measured_mm,
            item.explanation,
        )
        for item in report.results
    ]
    keys_b = [
        (
            item.severity.value,
            item.rule_id,
            tuple(item.entity_ids),
            item.measured_mm,
            item.explanation,
        )
        for item in reordered.results
    ]
    assert keys_a == keys_b
    # And the output itself is already in the contract sort order.
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    sort_keys = [
        (
            severity_rank[item.severity.value],
            item.rule_id,
            ",".join(sorted(item.entity_ids)),
            item.explanation,
        )
        for item in report.results
    ]
    assert sort_keys == sorted(sort_keys)


def test_enabled_rules_filter() -> None:
    scene = _validation_scene()
    config = ValidationConfig(enabled_rules=[RULE_OBJECT_COLLISION])
    report = validate_scene(scene, scene_revision_id="rev-1", config=config)
    assert {item.rule_id for item in report.results} <= {RULE_OBJECT_COLLISION}
