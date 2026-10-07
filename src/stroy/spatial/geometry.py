"""Floor-projected 2D geometry for the spatial validator (pure python).

Everything lives in the floor plane (X/Y, Z-up world): object footprints are
oriented rectangles (OBB) derived from ``transform.translation_mm`` /
``transform.rotation_deg`` (Z yaw) / ``geometry.dimensions_mm`` [width, depth,
height]; walls are oriented rectangles derived from ``geometry.a``/``b``
endpoints and ``dimensions_mm.thickness``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from stroy.domain.models import SceneEntity

# Kinds excluded from the editable-object rule set (construction shell).
SHELL_KINDS = frozenset({"wall", "floor", "ceiling", "door", "window"})

# Overlap tolerance in mm: near-touching footprints (flush placement against a
# wall face) are not overlaps. Distance-based rules report the real gap.
EPS_OVERLAP_MM = 1e-6

Point = tuple[float, float]
Polygon = list[Point]


def _perpendicular(vector: Point) -> Point:
    return (-vector[1], vector[0])


def _unit_direction(dx: float, dy: float) -> Point:
    length = math.hypot(dx, dy)
    if length <= 0:
        return (1.0, 0.0)
    return (dx / length, dy / length)


@dataclass(frozen=True)
class Rect:
    """Oriented rectangle (OBB) in the floor plane, millimetres."""

    center: Point
    axis_u: Point  # unit vector along local X (width)
    axis_v: Point  # unit vector along local Y (depth)
    half_u: float
    half_v: float

    def corners(self) -> Polygon:
        cx, cy = self.center
        ux, uy = self.axis_u
        vx, vy = self.axis_v
        hu, hv = self.half_u, self.half_v
        return [
            (cx + ux * hu + vx * hv, cy + uy * hu + vy * hv),
            (cx - ux * hu + vx * hv, cy - uy * hu + vy * hv),
            (cx - ux * hu - vx * hv, cy - uy * hu - vy * hv),
            (cx + ux * hu - vx * hv, cy + uy * hu - vy * hv),
        ]


def object_footprint(entity: SceneEntity) -> Rect | None:
    """Floor-projected OBB of an editable object.

    Requires ``geometry.dimensions_mm`` as ``[width, depth, height]``; entities
    without usable dimensions (rooms, plan openings, shell pieces without dims)
    have no footprint and are skipped by footprint rules.
    """
    geometry = entity.geometry if isinstance(entity.geometry, dict) else {}
    raw = geometry.get("dimensions_mm")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        width = float(raw[0])
        depth = float(raw[1])
    except (TypeError, ValueError):
        return None
    scale = entity.transform.scale
    half_u = abs(width * scale[0]) / 2.0
    half_v = abs(depth * scale[1]) / 2.0
    if half_u <= 0 or half_v <= 0:
        return None
    angle = math.radians(float(entity.transform.rotation_deg[2]))
    cx = float(entity.transform.translation_mm[0])
    cy = float(entity.transform.translation_mm[1])
    axis_u = (math.cos(angle), math.sin(angle))
    return Rect(
        center=(cx, cy),
        axis_u=axis_u,
        axis_v=_perpendicular(axis_u),
        half_u=half_u,
        half_v=half_v,
    )


def wall_rect(wall: SceneEntity) -> Rect | None:
    """Oriented wall rectangle from a/b endpoints + dimensions_mm.thickness."""
    geometry = wall.geometry if isinstance(wall.geometry, dict) else {}
    a = geometry.get("a")
    b = geometry.get("b")
    dims = geometry.get("dimensions_mm")
    if not isinstance(a, (list, tuple)) or len(a) < 2:
        return None
    if not isinstance(b, (list, tuple)) or len(b) < 2:
        return None
    if not isinstance(dims, dict):
        return None
    raw_thickness = dims.get("thickness")
    if raw_thickness is None:
        return None
    try:
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        thickness = float(raw_thickness)
    except (TypeError, ValueError):
        return None
    if math.hypot(bx - ax, by - ay) <= 0 or thickness <= 0:
        return None
    axis_u = _unit_direction(bx - ax, by - ay)
    return Rect(
        center=((ax + bx) / 2.0, (ay + by) / 2.0),
        axis_u=axis_u,
        axis_v=_perpendicular(axis_u),
        half_u=math.hypot(bx - ax, by - ay) / 2.0,
        half_v=thickness / 2.0,
    )


@dataclass(frozen=True)
class OpeningClearance:
    """Passage clearance rect for an opening positioned at ``t`` on its wall."""

    entity_id: str
    kind: str  # door / window / arch
    rect: Rect
    width_mm: float
    hinge_start: Point
    hinge_end: Point
    axis_u: Point


def opening_clearance(opening: SceneEntity, wall: SceneEntity) -> OpeningClearance | None:
    """Derive the opening center on the wall line and its clearance rect."""
    geometry = opening.geometry if isinstance(opening.geometry, dict) else {}
    host_wall_id = geometry.get("host_wall_id")
    if not isinstance(host_wall_id, str) or host_wall_id != wall.id:
        return None
    wall_geometry = wall.geometry if isinstance(wall.geometry, dict) else {}
    a = wall_geometry.get("a")
    b = wall_geometry.get("b")
    wall_dims = wall_geometry.get("dimensions_mm")
    if not isinstance(a, (list, tuple)) or len(a) < 2:
        return None
    if not isinstance(b, (list, tuple)) or len(b) < 2:
        return None
    if not isinstance(wall_dims, dict):
        return None
    raw_t = geometry.get("t")
    raw_width = geometry.get("width_mm")
    raw_thickness = wall_dims.get("thickness")
    if raw_t is None or raw_width is None or raw_thickness is None:
        return None
    try:
        t = float(raw_t)
        width = float(raw_width)
        thickness = float(raw_thickness)
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
    except (TypeError, ValueError):
        return None
    if not (0.0 <= t <= 1.0) or width <= 0 or thickness <= 0:
        return None
    if math.hypot(bx - ax, by - ay) <= 0:
        return None
    axis_u = _unit_direction(bx - ax, by - ay)
    center = (ax + t * (bx - ax), ay + t * (by - ay))
    half_u = width / 2.0
    kind = geometry.get("opening_kind") or opening.metadata.get("opening_kind")
    if not isinstance(kind, str) or not kind:
        kind = opening.kind.value
    return OpeningClearance(
        entity_id=opening.id,
        kind=kind,
        rect=Rect(
            center=center,
            axis_u=axis_u,
            axis_v=_perpendicular(axis_u),
            half_u=half_u,
            half_v=thickness / 2.0,
        ),
        width_mm=width,
        hinge_start=(
            center[0] - axis_u[0] * half_u,
            center[1] - axis_u[1] * half_u,
        ),
        hinge_end=(center[0] + axis_u[0] * half_u, center[1] + axis_u[1] * half_u),
        axis_u=axis_u,
    )


def polygon_vertices(rect: Rect) -> Polygon:
    """Corner polygon of an oriented rectangle."""
    return rect.corners()


def _project(polygon: Polygon, axis: Point) -> tuple[float, float]:
    projections = [px * axis[0] + py * axis[1] for px, py in polygon]
    return min(projections), max(projections)


def _axes(polygon: Polygon) -> list[Point]:
    axes: list[Point] = []
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        axis = (dy / length, -dx / length)
        if all(
            abs(axis[0] * prev[0] + axis[1] * prev[1]) < 1.0 - 1e-9 for prev in axes
        ):
            axes.append(axis)
    return axes


def polygons_overlap(first: Polygon, second: Polygon) -> bool:
    """SAT intersection test for convex polygons (exact touching is no overlap)."""
    for polygon in (first, second):
        for axis in _axes(polygon):
            a_min, a_max = _project(first, axis)
            b_min, b_max = _project(second, axis)
            if a_max <= b_min + EPS_OVERLAP_MM or b_max <= a_min + EPS_OVERLAP_MM:
                return False
    return True


def point_segment_distance(point: Point, start: Point, end: Point) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(p: Point, q: Point, r: Point) -> bool:
    return (
        min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
        and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
        and abs(_orientation(p, q, r)) <= 1e-9
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1 = _orientation(c, d, a)
    d2 = _orientation(c, d, b)
    d3 = _orientation(a, b, c)
    d4 = _orientation(a, b, d)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return (
        _on_segment(c, a, d)
        or _on_segment(c, b, d)
        or _on_segment(a, c, b)
        or _on_segment(a, d, b)
    )


def segment_distance(first: tuple[Point, Point], second: tuple[Point, Point]) -> float:
    a, b = first
    c, d = second
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


def polygon_distance(first: Polygon, second: Polygon) -> float:
    """Minimum distance in mm between two convex polygons (0 when overlapping)."""
    if polygons_overlap(first, second):
        return 0.0
    best = float("inf")
    count_a = len(first)
    count_b = len(second)
    for i in range(count_a):
        edge_a = (first[i], first[(i + 1) % count_a])
        for j in range(count_b):
            edge_b = (second[j], second[(j + 1) % count_b])
            best = min(best, segment_distance(edge_a, edge_b))
    return best


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    px, py = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > py) != (y2 > py):
            intersect_x = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < intersect_x:
                inside = not inside
    return inside


def arc_sector_polygon(
    hinge: Point,
    start_direction: Point,
    end_direction: Point,
    radius_mm: float,
    segments: int = 8,
) -> Polygon:
    """Convex fan approximation of a circular sector (sweep <= 180 degrees)."""
    start_angle = math.atan2(start_direction[1], start_direction[0])
    end_angle = math.atan2(end_direction[1], end_direction[0])
    delta = (end_angle - start_angle + math.pi * 3) % (math.pi * 2) - math.pi
    polygon: Polygon = [hinge]
    for step in range(segments + 1):
        angle = start_angle + delta * step / segments
        polygon.append(
            (
                hinge[0] + radius_mm * math.cos(angle),
                hinge[1] + radius_mm * math.sin(angle),
            )
        )
    return polygon
