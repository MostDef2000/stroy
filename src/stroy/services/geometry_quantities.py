"""Pure geometry takeoff quantities (R4).

Deterministic, DB-free helpers over the canonical scene shapes produced by
``services.plans``:

- floor/ceiling entities carry ``geometry.bbox`` ``[minx, miny, maxx, maxy]``
- walls carry ``geometry.dimensions_mm`` ``{length, thickness, height}`` and
  endpoints ``geometry.a`` / ``geometry.b``
- openings carry ``geometry.host_wall_id``, ``width_mm``, ``height_mm`` and an
  ``EntityKind`` of door / window / architectural

Every function returns ``None`` ("unknown") when the geometry it needs is
missing or malformed — never 0, so budget takeoff can flag an item incomplete
instead of silently pricing it as free.
"""

from __future__ import annotations

import math

from stroy.domain.models import EntityKind, Scene, SceneEntity

# Openings that interrupt the wall surface (and thus wall area).
_VERTICAL_OPENINGS = {EntityKind.DOOR, EntityKind.WINDOW, EntityKind.ARCHITECTURAL}
# Openings that reach the floor and interrupt skirting boards only.
_BASEBOARD_OPENINGS = {EntityKind.DOOR, EntityKind.ARCHITECTURAL}


def _finite(value: object) -> float | None:
    """Guarded numeric cast: only real int/float (bool excluded), finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    x = _finite(value[0])
    y = _finite(value[1])
    if x is None or y is None:
        return None
    return (x, y)


def _bbox_area_mm2(entity: SceneEntity) -> float | None:
    bbox = entity.geometry.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    min_x = _finite(bbox[0])
    min_y = _finite(bbox[1])
    max_x = _finite(bbox[2])
    max_y = _finite(bbox[3])
    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return (max_x - min_x) * (max_y - min_y)


def floor_area_mm2(entity: SceneEntity) -> float | None:
    """Floor (bbox-bounded horizontal surface) area in mm²."""
    if entity.kind is not EntityKind.FLOOR:
        return None
    return _bbox_area_mm2(entity)


def ceiling_area_mm2(entity: SceneEntity) -> float | None:
    """Ceiling area in mm² (same bbox shape as floor)."""
    if entity.kind is not EntityKind.CEILING:
        return None
    return _bbox_area_mm2(entity)


def wall_length_mm(entity: SceneEntity) -> float | None:
    """Wall length in mm from ``dimensions_mm.length`` (fallback: a→b)."""
    if entity.kind is not EntityKind.WALL:
        return None
    dims = entity.geometry.get("dimensions_mm")
    if isinstance(dims, dict):
        length = _finite(dims.get("length"))
        if length is not None:
            return length
    a = _point(entity.geometry.get("a"))
    b = _point(entity.geometry.get("b"))
    if a is None or b is None:
        return None
    return math.hypot(b[0] - a[0], b[1] - a[1])


def wall_height_mm(entity: SceneEntity) -> float | None:
    """Wall height in mm from ``dimensions_mm.height``."""
    if entity.kind is not EntityKind.WALL:
        return None
    dims = entity.geometry.get("dimensions_mm")
    if isinstance(dims, dict):
        return _finite(dims.get("height"))
    return None


def _entity_by_id(scene: Scene, entity_id: str) -> SceneEntity | None:
    return next(
        (entity for entity in scene.entities if entity.id == entity_id), None
    )


def _room_wall_ids(scene: Scene, room_id: str) -> list[str] | None:
    """Wall ids of the room, or None when the room has no known walls."""
    room = next(
        (
            entity
            for entity in scene.entities
            if entity.id == room_id and entity.kind is EntityKind.ROOM
        ),
        None,
    )
    if room is not None:
        wall_ids = room.geometry.get("wall_ids")
        if isinstance(wall_ids, list) and wall_ids:
            return [str(wall_id) for wall_id in wall_ids]
    # Fallback: walls tagged with the room (scenes built outside plans).
    fallback = [
        entity.id
        for entity in scene.entities
        if entity.kind is EntityKind.WALL and entity.room_id == room_id
    ]
    return fallback or None


def _openings_for_walls(scene: Scene, wall_ids: set[str]) -> list[SceneEntity]:
    return [
        entity
        for entity in scene.entities
        if entity.kind in _VERTICAL_OPENINGS
        and entity.geometry.get("host_wall_id") in wall_ids
    ]


def wall_area_mm2(scene: Scene, room_id: str) -> float | None:
    """Vertical wall surface of a room in mm².

    Σ(wall.length × wall.height) − Σ(opening.width × opening.height) over the
    room's walls and their hosted openings. Unknown member → None.
    """
    wall_ids = _room_wall_ids(scene, room_id)
    if wall_ids is None:
        return None
    walls: list[SceneEntity] = []
    for wall_id in wall_ids:
        wall = _entity_by_id(scene, wall_id)
        if wall is None or wall.kind is not EntityKind.WALL:
            return None
        walls.append(wall)
    total = 0.0
    for wall in walls:
        length = wall_length_mm(wall)
        height = wall_height_mm(wall)
        if length is None or height is None:
            return None
        total += length * height
    for opening in _openings_for_walls(scene, {wall.id for wall in walls}):
        width = _finite(opening.geometry.get("width_mm"))
        height = _finite(opening.geometry.get("height_mm"))
        if width is None or height is None:
            return None
        total -= width * height
    return total


def baseboard_length_mm(scene: Scene, room_id: str) -> float | None:
    """Skirting-board run of a room in mm.

    Σ(wall lengths) − Σ(door/arch widths): windows do not interrupt the
    skirting line. Unknown member → None.
    """
    wall_ids = _room_wall_ids(scene, room_id)
    if wall_ids is None:
        return None
    walls: list[SceneEntity] = []
    for wall_id in wall_ids:
        wall = _entity_by_id(scene, wall_id)
        if wall is None or wall.kind is not EntityKind.WALL:
            return None
        walls.append(wall)
    total = 0.0
    for wall in walls:
        length = wall_length_mm(wall)
        if length is None:
            return None
        total += length
    for opening in _openings_for_walls(scene, {wall.id for wall in walls}):
        if opening.kind not in _BASEBOARD_OPENINGS:
            continue
        width = _finite(opening.geometry.get("width_mm"))
        if width is None:
            return None
        total -= width
    return total
