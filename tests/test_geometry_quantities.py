"""Geometry takeoff quantity tests (R4): hand-computed expectations.

Fixture scene: a 5000×4000 mm room, walls 2700 mm high, one door
(900×2100) and one window (1200×1500). All expectations are derived by
hand — the helpers must never be "verified" against themselves.
"""

from __future__ import annotations

import pytest

from stroy.domain.models import EntityKind, Scene, SceneEntity
from stroy.services.geometry_quantities import (
    baseboard_length_mm,
    ceiling_area_mm2,
    floor_area_mm2,
    wall_area_mm2,
    wall_height_mm,
    wall_length_mm,
)

WALL_IDS = ["wall.north", "wall.south", "wall.west", "wall.east"]
# (2 × 5000 + 2 × 4000) × 2700
FULL_WALL_AREA = 48_600_000.0
# − door 900×2100 − window 1200×1500
ROOM_WALL_AREA = 44_910_000.0
# 18000 − door 900 (windows do not interrupt skirting)
ROOM_BASEBOARD = 17_100.0


def _wall(wall_id: str, a: list[float], b: list[float], length: float) -> SceneEntity:
    return SceneEntity(
        id=wall_id,
        kind=EntityKind.WALL,
        room_id="room.living",
        geometry={
            "a": a,
            "b": b,
            "dimensions_mm": {"length": length, "thickness": 100, "height": 2700},
        },
    )


def _full_scene(*, with_openings: bool = True) -> Scene:
    entities = [
        SceneEntity(
            id="room.living",
            kind=EntityKind.ROOM,
            geometry={"wall_ids": list(WALL_IDS)},
        ),
        _wall("wall.north", [0, 4000], [5000, 4000], 5000),
        _wall("wall.south", [0, 0], [5000, 0], 5000),
        _wall("wall.west", [0, 0], [0, 4000], 4000),
        _wall("wall.east", [5000, 0], [5000, 4000], 4000),
        SceneEntity(
            id="floor.main",
            kind=EntityKind.FLOOR,
            room_id="room.living",
            geometry={"bbox": [0, 0, 5000, 4000]},
        ),
        SceneEntity(
            id="ceiling.main",
            kind=EntityKind.CEILING,
            room_id="room.living",
            geometry={"bbox": [0, 0, 5000, 4000]},
        ),
    ]
    if with_openings:
        entities.append(
            SceneEntity(
                id="door.main",
                kind=EntityKind.DOOR,
                room_id="room.living",
                geometry={
                    "host_wall_id": "wall.south",
                    "width_mm": 900,
                    "height_mm": 2100,
                    "sill_mm": 0,
                },
            )
        )
        entities.append(
            SceneEntity(
                id="window.main",
                kind=EntityKind.WINDOW,
                room_id="room.living",
                geometry={
                    "host_wall_id": "wall.north",
                    "width_mm": 1200,
                    "height_mm": 1500,
                    "sill_mm": 900,
                },
            )
        )
    return Scene(scene_id="scene.main", project_id="p1", entities=entities)


def test_floor_and_ceiling_bbox_area() -> None:
    scene = _full_scene()
    floor = next(e for e in scene.entities if e.id == "floor.main")
    ceiling = next(e for e in scene.entities if e.id == "ceiling.main")
    assert floor_area_mm2(floor) == pytest.approx(20_000_000.0)
    assert ceiling_area_mm2(ceiling) == pytest.approx(20_000_000.0)
    # Kind mismatch → unknown, never 0.
    assert floor_area_mm2(ceiling) is None
    assert ceiling_area_mm2(floor) is None


def test_wall_length_uses_dimensions_with_endpoint_fallback() -> None:
    scene = _full_scene()
    north = next(e for e in scene.entities if e.id == "wall.north")
    assert wall_length_mm(north) == pytest.approx(5000.0)
    assert wall_height_mm(north) == pytest.approx(2700.0)

    # Fallback: 3-4-5 endpoints → hypot length, no dimensions_mm at all.
    diagonal = SceneEntity(
        id="wall.diag",
        kind=EntityKind.WALL,
        geometry={"a": [0, 0], "b": [3000, 4000]},
    )
    assert wall_length_mm(diagonal) == pytest.approx(5000.0)
    assert wall_height_mm(diagonal) is None


def test_wall_area_subtracts_openings() -> None:
    scene = _full_scene()
    assert wall_area_mm2(scene, "room.living") == pytest.approx(ROOM_WALL_AREA)

    plain = _full_scene(with_openings=False)
    assert wall_area_mm2(plain, "room.living") == pytest.approx(FULL_WALL_AREA)


def test_baseboard_skips_windows_but_not_doors() -> None:
    scene = _full_scene()
    assert baseboard_length_mm(scene, "room.living") == pytest.approx(ROOM_BASEBOARD)


def test_unknown_geometry_members_yield_none() -> None:
    # Wall without dimensions or endpoints → length unknown.
    naked_wall = SceneEntity(id="wall.x", kind=EntityKind.WALL, geometry={})
    assert wall_length_mm(naked_wall) is None
    assert wall_height_mm(naked_wall) is None

    # Floor without a usable bbox.
    broken_floor = SceneEntity(
        id="floor.broken", kind=EntityKind.FLOOR, geometry={"bbox": [0, 0]}
    )
    assert floor_area_mm2(broken_floor) is None

    scene = _full_scene()
    # Opening missing its width makes the room's wall area unknown...
    scene.entities[-1].geometry.pop("width_mm")
    assert wall_area_mm2(scene, "room.living") is None
    # ...and (as a door) the skirting run too.
    scene.entities[-1].kind = EntityKind.DOOR
    assert baseboard_length_mm(scene, "room.living") is None


def test_room_without_known_walls_is_unknown() -> None:
    scene = _full_scene()
    scene.entities[0].geometry = {}  # drop wall_ids from the room...
    for entity in scene.entities:
        if entity.kind is EntityKind.WALL:
            entity.room_id = None  # ...and strip the room_id fallback tags
    assert wall_area_mm2(scene, "room.living") is None
    assert baseboard_length_mm(scene, "room.living") is None


def test_room_wall_fallback_via_room_tagging() -> None:
    scene = _full_scene()
    scene.entities[0].geometry = {}  # no wall_ids on the room
    # Walls carry room_id="room.living" → the fallback path resolves them.
    assert wall_area_mm2(scene, "room.living") == pytest.approx(ROOM_WALL_AREA)
    assert baseboard_length_mm(scene, "room.living") == pytest.approx(ROOM_BASEBOARD)


def test_architectural_opening_counts_like_a_door() -> None:
    scene = _full_scene(with_openings=False)
    scene.entities.append(
        SceneEntity(
            id="arch.pass",
            kind=EntityKind.ARCHITECTURAL,
            room_id="room.living",
            geometry={
                "host_wall_id": "wall.west",
                "width_mm": 1000,
                "height_mm": 2100,
            },
        )
    )
    # Full area − arch 1000×2100; baseboard − 1000.
    assert wall_area_mm2(scene, "room.living") == pytest.approx(
        FULL_WALL_AREA - 2_100_000.0
    )
    assert baseboard_length_mm(scene, "room.living") == pytest.approx(
        18_000.0 - 1000.0
    )
