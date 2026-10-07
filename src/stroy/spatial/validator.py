"""Deterministic spatial rules over a scene snapshot (R2).

Pure python, no database, no mutation: the validator consumes a ``Scene``
snapshot and returns a report that is a pure function of
(scene content, config). Results are sorted by

    severity (error > warning > info), rule_id,
    ",".join(sorted(entity_ids)), explanation,

so two runs over the same revision are byte-identical regardless of the
order entities appear in the scene.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from stroy.domain.models import EntityKind, Scene, SceneEntity
from stroy.spatial.geometry import (
    SHELL_KINDS,
    OpeningClearance,
    Point,
    Polygon,
    Rect,
    arc_sector_polygon,
    object_footprint,
    opening_clearance,
    polygon_distance,
    polygon_vertices,
    polygons_overlap,
    wall_rect,
)
from stroy.spatial.models import (
    CheckResult,
    CheckSeverity,
    ValidationConfig,
    ValidationReport,
    ValidationSummary,
)

RULE_OBJECT_COLLISION = "object.object_collision"
RULE_WALL_COLLISION = "object.wall_collision"
RULE_OUTSIDE_ROOM = "object.outside_room_bounds"
RULE_OPENING_BLOCKED = "opening.blocked"
RULE_WALKWAY_MIN = "clearance.walkway_min"
RULE_DOOR_SWING = "envelope.door_swing"
RULE_DOOR_SWING_UNCHECKED = "envelope.door_swing_unchecked"
RULE_CHAIR_PULLOUT = "envelope.chair_pullout"
RULE_BED_ACCESS = "envelope.bed_access"
RULE_SOFA_ACCESS = "envelope.sofa_access"
RULE_CABINET_OPENING = "envelope.cabinet_opening"

# Gated rule ids (envelope.door_swing_unchecked shares the door_swing gate).
RULE_IDS = (
    RULE_OBJECT_COLLISION,
    RULE_WALL_COLLISION,
    RULE_OUTSIDE_ROOM,
    RULE_OPENING_BLOCKED,
    RULE_WALKWAY_MIN,
    RULE_DOOR_SWING,
    RULE_CHAIR_PULLOUT,
    RULE_BED_ACCESS,
    RULE_SOFA_ACCESS,
    RULE_CABINET_OPENING,
)

# Envelope clearances (mm).
CHAIR_PULLOUT_MM = 750.0
BED_ACCESS_MM = 600.0
SOFA_ACCESS_MM = 600.0

# A door counts as blocked when an object covers this share of the passage.
DOOR_BLOCKED_COVERAGE = 0.6

# Bisection resolution for "how much of a required clearance is free".
_DEPTH_PRECISION_MM = 1.0
_DEPTH_STEPS = 24

_OPENING_KINDS = frozenset(
    {EntityKind.DOOR, EntityKind.WINDOW, EntityKind.ARCHITECTURAL}
)

_SEVERITY_ORDER = {
    CheckSeverity.ERROR: 0,
    CheckSeverity.WARNING: 1,
    CheckSeverity.INFO: 2,
}


def canonical_json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scene_content_hash(scene: Scene) -> str:
    """sha256 over the canonical JSON of the full scene dump (nulls kept)."""
    return canonical_json_hash(scene.model_dump(mode="json"))


def config_hash(config: ValidationConfig) -> str:
    return canonical_json_hash(config.model_dump(mode="json"))


def report_hash(report_json: dict[str, Any]) -> str:
    return canonical_json_hash(report_json)


def _sort_key(result: CheckResult) -> tuple[int, str, str, str, float]:
    return (
        _SEVERITY_ORDER[result.severity],
        result.rule_id,
        ",".join(sorted(result.entity_ids)),
        result.explanation,
        result.measured_mm if result.measured_mm is not None else -1.0,
    )


def _emit(results: list[CheckResult], result: CheckResult) -> None:
    # Canonical entity order inside every result keeps report bytes stable.
    results.append(result.model_copy(update={"entity_ids": sorted(result.entity_ids)}))


def _is_editable(entity: SceneEntity) -> bool:
    return entity.kind.value not in SHELL_KINDS


def _category_text(entity: SceneEntity) -> str:
    """Lowercased haystack for category matching (kind, id, name, metadata)."""
    parts = [entity.kind.value, entity.id, entity.display_name or ""]
    for key, value in entity.metadata.items():
        parts.append(str(key))
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
    return " ".join(parts).lower()


def _strip_polygon(
    footprint: Rect,
    *,
    outward: Point,
    lateral: Point,
    lateral_half: float,
    depth: float,
) -> Polygon:
    """Clearance strip adjacent to the footprint, extending ``depth`` outward.

    ``outward`` is a unit world vector (typically ± the footprint's local v
    or u axis); the strip spans ``lateral_half * 2`` along ``lateral`` and is
    attached to the footprint edge facing ``outward``.
    """
    if depth <= 0:
        return []
    # Distance from the footprint center to its boundary along ``outward``:
    # the support function of the OBB (exact for any direction).
    edge_offset = footprint.half_u * abs(
        outward[0] * footprint.axis_u[0] + outward[1] * footprint.axis_u[1]
    ) + footprint.half_v * abs(
        outward[0] * footprint.axis_v[0] + outward[1] * footprint.axis_v[1]
    )
    center = (
        footprint.center[0] + outward[0] * (edge_offset + depth / 2.0),
        footprint.center[1] + outward[1] * (edge_offset + depth / 2.0),
    )
    return polygon_vertices(
        Rect(
            center=center,
            axis_u=lateral,
            axis_v=outward,
            half_u=lateral_half,
            half_v=depth / 2.0,
        )
    )


def _depth_blocked(
    footprint: Rect,
    *,
    outward: Point,
    lateral: Point,
    lateral_half: float,
    depth: float,
    blockers: list[Polygon],
) -> bool:
    polygon = _strip_polygon(
        footprint,
        outward=outward,
        lateral=lateral,
        lateral_half=lateral_half,
        depth=depth,
    )
    if not polygon:
        return False
    return any(polygons_overlap(polygon, blocker) for blocker in blockers)


def _max_clear_depth(
    footprint: Rect,
    *,
    outward: Point,
    lateral: Point,
    lateral_half: float,
    required_mm: float,
    blockers: list[Polygon],
) -> float:
    """Largest depth <= required whose strip hits no blocker.

    Blocking is monotone in depth (a deeper strip contains a shallower one),
    so bisection over [0, required] is exact for practical sizes. Returns
    ``required_mm`` when the full strip is clear.
    """
    if required_mm <= 0:
        return 0.0

    def blocked(depth: float) -> bool:
        return _depth_blocked(
            footprint,
            outward=outward,
            lateral=lateral,
            lateral_half=lateral_half,
            depth=depth,
            blockers=blockers,
        )

    if not blocked(required_mm):
        return required_mm
    low, high = 0.0, required_mm
    for _ in range(_DEPTH_STEPS):
        if high - low <= _DEPTH_PRECISION_MM:
            break
        mid = (low + high) / 2.0
        if blocked(mid):
            high = mid
        else:
            low = mid
    return low


def _clearance_result(
    *,
    rule_id: str,
    entity: SceneEntity,
    footprint: Rect,
    outward: Point,
    lateral: Point,
    lateral_half: float,
    required_mm: float,
    named_blockers: list[tuple[str, Polygon]],
    explanation_available: str,
    suggestion_template: str,
) -> CheckResult | None:
    """Build the warning result when a required clearance strip is blocked."""
    depth = _max_clear_depth(
        footprint,
        outward=outward,
        lateral=lateral,
        lateral_half=lateral_half,
        required_mm=required_mm,
        blockers=[polygon for _, polygon in named_blockers],
    )
    if depth >= required_mm - 1e-6:
        return None
    full_strip = _strip_polygon(
        footprint,
        outward=outward,
        lateral=lateral,
        lateral_half=lateral_half,
        depth=required_mm,
    )
    blocking_ids = sorted(
        owner_id
        for owner_id, polygon in named_blockers
        if polygons_overlap(full_strip, polygon)
    )
    deficit = math.ceil(required_mm - depth)
    return CheckResult(
        severity=CheckSeverity.WARNING,
        entity_ids=[entity.id, *blocking_ids],
        rule_id=rule_id,
        measured_mm=round(depth, 3),
        expected_min_mm=required_mm,
        explanation=explanation_available.format(
            entity_id=entity.id, available=depth, required=required_mm
        ),
        suggestion=suggestion_template.format(deficit=max(deficit, 1)),
    )


def validate_scene(
    scene: Scene,
    *,
    scene_revision_id: str,
    config: ValidationConfig | None = None,
) -> ValidationReport:
    active = config or ValidationConfig()
    enabled = set(active.enabled_rules) if active.enabled_rules is not None else None

    def gate(rule: str) -> bool:
        return enabled is None or rule in enabled

    results: list[CheckResult] = []

    objects: list[tuple[SceneEntity, Rect]] = []
    for entity in scene.entities:
        if not _is_editable(entity):
            continue
        footprint = object_footprint(entity)
        if footprint is not None:
            objects.append((entity, footprint))
    objects.sort(key=lambda item: item[0].id)

    walls: list[tuple[SceneEntity, Rect]] = []
    for entity in scene.entities:
        if entity.kind is not EntityKind.WALL:
            continue
        rect = wall_rect(entity)
        if rect is not None:
            walls.append((entity, rect))
    walls.sort(key=lambda item: item[0].id)
    wall_polygons: list[tuple[str, Polygon]] = [
        (wall.id, polygon_vertices(rect)) for wall, rect in walls
    ]
    object_polygons: list[tuple[str, Polygon]] = [
        (entity.id, polygon_vertices(footprint)) for entity, footprint in objects
    ]

    walls_by_id = {wall.id: wall for wall, _ in walls}
    openings: list[OpeningClearance] = []
    for entity in scene.entities:
        if entity.kind not in _OPENING_KINDS:
            continue
        host_id = entity.geometry.get("host_wall_id")
        if not isinstance(host_id, str):
            continue
        host = walls_by_id.get(host_id)
        if host is None:
            continue
        clearance = opening_clearance(entity, host)
        if clearance is not None:
            openings.append(clearance)
    openings.sort(key=lambda item: item.entity_id)

    # -- object.object_collision -------------------------------------------
    if gate(RULE_OBJECT_COLLISION):
        for i in range(len(objects)):
            left_entity, left_rect = objects[i]
            left_polygon = polygon_vertices(left_rect)
            for j in range(i + 1, len(objects)):
                right_entity, right_rect = objects[j]
                if polygons_overlap(left_polygon, polygon_vertices(right_rect)):
                    _emit(
                        results,
                        CheckResult(
                            severity=CheckSeverity.ERROR,
                            entity_ids=[left_entity.id, right_entity.id],
                            rule_id=RULE_OBJECT_COLLISION,
                            measured_mm=None,
                            explanation=(
                                f"footprints of {left_entity.id} and "
                                f"{right_entity.id} overlap"
                            ),
                            suggestion="разведите объекты так, чтобы их габариты не пересекались",
                        ),
                    )

    # -- object.wall_collision ---------------------------------------------
    if gate(RULE_WALL_COLLISION):
        for entity, footprint in objects:
            footprint_polygon = polygon_vertices(footprint)
            for wall_id, wall_polygon in wall_polygons:
                if polygons_overlap(footprint_polygon, wall_polygon):
                    _emit(
                        results,
                        CheckResult(
                            severity=CheckSeverity.ERROR,
                            entity_ids=[entity.id, wall_id],
                            rule_id=RULE_WALL_COLLISION,
                            measured_mm=None,
                            explanation=f"{entity.id} overlaps wall {wall_id}",
                            suggestion="отодвиньте объект от стены",
                        ),
                    )

    # -- object.outside_room_bounds ----------------------------------------
    if gate(RULE_OUTSIDE_ROOM):
        floors_by_room: dict[str, tuple[float, float, float, float]] = {}
        for entity in scene.entities:
            if entity.kind is not EntityKind.FLOOR or entity.room_id is None:
                continue
            bbox = entity.geometry.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                floors_by_room[entity.room_id] = (
                    float(bbox[0]),
                    float(bbox[1]),
                    float(bbox[2]),
                    float(bbox[3]),
                )
            except (TypeError, ValueError):
                continue
        for entity, footprint in objects:
            if entity.room_id is None:
                continue
            bbox = floors_by_room.get(entity.room_id)
            if bbox is None:
                # No floor bbox for the room: the rule is skipped entirely.
                continue
            min_x, min_y, max_x, max_y = bbox
            overshoot = 0.0
            for px, py in footprint.corners():
                overshoot = max(
                    overshoot,
                    min_x - px,
                    px - max_x,
                    min_y - py,
                    py - max_y,
                )
            if overshoot > 1e-6:
                _emit(
                    results,
                    CheckResult(
                        severity=CheckSeverity.ERROR,
                        entity_ids=[entity.id],
                        rule_id=RULE_OUTSIDE_ROOM,
                        measured_mm=round(overshoot, 3),
                        explanation=(
                            f"{entity.id} extends {overshoot:.0f} mm outside the "
                            f"floor bounds of room {entity.room_id}"
                        ),
                        suggestion="переместите объект внутрь границ помещения",
                    ),
                )

    # -- opening.blocked ----------------------------------------------------
    if gate(RULE_OPENING_BLOCKED):
        for opening in openings:
            clearance_polygon = polygon_vertices(opening.rect)
            center_offset = (
                opening.rect.center[0] * opening.axis_u[0]
                + opening.rect.center[1] * opening.axis_u[1]
            )
            for entity, footprint in objects:
                if not polygons_overlap(polygon_vertices(footprint), clearance_polygon):
                    continue
                projections = [
                    px * opening.axis_u[0] + py * opening.axis_u[1]
                    for px, py in footprint.corners()
                ]
                # True interval intersection along the opening axis:
                # [max(min_proj, opening_min), min(max_proj, opening_max)].
                opening_min = center_offset - opening.width_mm / 2.0
                opening_max = center_offset + opening.width_mm / 2.0
                overlap = min(max(projections), opening_max) - max(
                    min(projections), opening_min
                )
                overlap = max(0.0, min(overlap, opening.width_mm))
                coverage = overlap / opening.width_mm if opening.width_mm > 0 else 0.0
                is_door = opening.kind == "door"
                if is_door and coverage >= DOOR_BLOCKED_COVERAGE:
                    severity = CheckSeverity.ERROR
                else:
                    severity = CheckSeverity.WARNING
                _emit(
                    results,
                    CheckResult(
                        severity=severity,
                        entity_ids=[opening.entity_id, entity.id],
                        rule_id=RULE_OPENING_BLOCKED,
                        measured_mm=round(overlap, 3),
                        explanation=(
                            f"{entity.id} blocks {coverage * 100:.0f}% of the "
                            f"{opening.kind} passage {opening.entity_id}"
                        ),
                        suggestion="освободите проход у проёма",
                    ),
                )

    # -- clearance.walkway_min ----------------------------------------------
    if gate(RULE_WALKWAY_MIN):
        threshold = active.min_walkway_mm
        for entity, footprint in objects:
            footprint_polygon = polygon_vertices(footprint)
            candidates: list[tuple[float, str]] = []
            for other_id, other_polygon in object_polygons:
                if other_id == entity.id:
                    continue
                # Overlapping pairs are object_collision errors, not walkways.
                if polygons_overlap(footprint_polygon, other_polygon):
                    continue
                gap = polygon_distance(footprint_polygon, other_polygon)
                if 0.0 <= gap < threshold:
                    candidates.append((gap, other_id))
            for wall_id, wall_polygon in wall_polygons:
                if polygons_overlap(footprint_polygon, wall_polygon):
                    continue
                gap = polygon_distance(footprint_polygon, wall_polygon)
                if 0.0 <= gap < threshold:
                    candidates.append((gap, wall_id))
            if not candidates:
                continue
            worst_gap, other_id = min(candidates, key=lambda item: (item[0], item[1]))
            deficit = math.ceil(threshold - worst_gap)
            _emit(
                results,
                CheckResult(
                    severity=CheckSeverity.WARNING,
                    entity_ids=[entity.id, other_id],
                    rule_id=RULE_WALKWAY_MIN,
                    measured_mm=round(worst_gap, 3),
                    expected_min_mm=threshold,
                    explanation=(
                        f"clearance between {entity.id} and {other_id} is "
                        f"{worst_gap:.0f} mm, below the {threshold:.0f} mm minimum"
                    ),
                    suggestion=f"переместите объект минимум на {max(deficit, 1)} мм",
                ),
            )

    # -- envelope.door_swing -------------------------------------------------
    if gate(RULE_DOOR_SWING):
        for opening in openings:
            if opening.kind != "door":
                continue
            hinge_side = _entity_metadata(scene, opening.entity_id).get("hinge")
            if hinge_side not in {"start", "end"}:
                _emit(
                    results,
                    CheckResult(
                        severity=CheckSeverity.INFO,
                        entity_ids=[opening.entity_id],
                        rule_id=RULE_DOOR_SWING_UNCHECKED,
                        measured_mm=None,
                        explanation=(
                            f"door swing unchecked for {opening.entity_id}: "
                            "hinge side metadata is missing"
                        ),
                    ),
                )
                continue
            hinge = opening.hinge_start if hinge_side == "start" else opening.hinge_end
            far_jamb = opening.hinge_end if hinge_side == "start" else opening.hinge_start
            along = (far_jamb[0] - hinge[0], far_jamb[1] - hinge[1])
            along_length = math.hypot(*along)
            if along_length <= 0:
                along = opening.axis_u
            else:
                along = (along[0] / along_length, along[1] / along_length)
            swing = _entity_metadata(scene, opening.entity_id).get("swing")
            sign = -1.0 if swing == "right" else 1.0
            normal = (-along[1] * sign, along[0] * sign)
            sector = arc_sector_polygon(hinge, along, normal, opening.width_mm)
            for entity, footprint in objects:
                if polygons_overlap(sector, polygon_vertices(footprint)):
                    _emit(
                        results,
                        CheckResult(
                            severity=CheckSeverity.WARNING,
                            entity_ids=[opening.entity_id, entity.id],
                            rule_id=RULE_DOOR_SWING,
                            measured_mm=None,
                            explanation=(
                                f"door swing arc of {opening.entity_id} is "
                                f"blocked by {entity.id}"
                            ),
                            suggestion="освободите зону открывания двери",
                        ),
                    )

    # -- envelope furniture clearances ---------------------------------------
    named_blockers = wall_polygons + object_polygons
    if gate(RULE_CHAIR_PULLOUT) or gate(RULE_SOFA_ACCESS) or gate(RULE_BED_ACCESS) or gate(
        RULE_CABINET_OPENING
    ):
        for entity, footprint in objects:
            category = _category(scene, entity)
            blockers = [
                (owner_id, polygon)
                for owner_id, polygon in named_blockers
                if owner_id != entity.id
            ]
            lateral = footprint.axis_u

            if gate(RULE_CHAIR_PULLOUT) and "chair" in category:
                result = _clearance_result(
                    rule_id=RULE_CHAIR_PULLOUT,
                    entity=entity,
                    footprint=footprint,
                    outward=(-footprint.axis_v[0], -footprint.axis_v[1]),
                    lateral=lateral,
                    lateral_half=footprint.half_u,
                    required_mm=CHAIR_PULLOUT_MM,
                    named_blockers=blockers,
                    explanation_available=(
                        "{entity_id} has {available:.0f} mm of the required "
                        "{required:.0f} mm pull-out clearance behind it"
                    ),
                    suggestion_template="освободите минимум {deficit} мм за стулом",
                )
                if result is not None:
                    _emit(results, result)

            if gate(RULE_SOFA_ACCESS) and "sofa" in category:
                result = _clearance_result(
                    rule_id=RULE_SOFA_ACCESS,
                    entity=entity,
                    footprint=footprint,
                    outward=(-footprint.axis_v[0], -footprint.axis_v[1]),
                    lateral=lateral,
                    lateral_half=footprint.half_u,
                    required_mm=SOFA_ACCESS_MM,
                    named_blockers=blockers,
                    explanation_available=(
                        "{entity_id} has {available:.0f} mm of the required "
                        "{required:.0f} mm clearance in front"
                    ),
                    suggestion_template="освободите минимум {deficit} мм перед диваном",
                )
                if result is not None:
                    _emit(results, result)

            if gate(RULE_BED_ACCESS) and "bed" in category:
                # Long sides: the two edges whose outward normal is the SHORT
                # local axis. The strip spans the bed's full long extent.
                if footprint.half_u >= footprint.half_v:
                    bed_lateral, bed_lateral_half = footprint.axis_u, footprint.half_u
                    outwards = [
                        (-footprint.axis_v[0], -footprint.axis_v[1]),
                        footprint.axis_v,
                    ]
                else:
                    bed_lateral, bed_lateral_half = footprint.axis_v, footprint.half_v
                    outwards = [
                        (-footprint.axis_u[0], -footprint.axis_u[1]),
                        footprint.axis_u,
                    ]
                best_depth = max(
                    _max_clear_depth(
                        footprint,
                        outward=outward,
                        lateral=bed_lateral,
                        lateral_half=bed_lateral_half,
                        required_mm=BED_ACCESS_MM,
                        blockers=[polygon for _, polygon in blockers],
                    )
                    for outward in outwards
                )
                if best_depth < BED_ACCESS_MM - 1e-6:
                    _emit(
                        results,
                        CheckResult(
                            severity=CheckSeverity.WARNING,
                            entity_ids=[entity.id],
                            rule_id=RULE_BED_ACCESS,
                            measured_mm=round(best_depth, 3),
                            expected_min_mm=BED_ACCESS_MM,
                            explanation=(
                                f"neither long side of {entity.id} has the "
                                f"required {BED_ACCESS_MM:.0f} mm access "
                                f"(best side: {best_depth:.0f} mm)"
                            ),
                            suggestion="освободите проход хотя бы с одной длинной стороны кровати",
                        ),
                    )

            raw_opening_depth = _entity_metadata(scene, entity.id).get(
                "opening_depth_mm"
            )
            if gate(RULE_CABINET_OPENING) and isinstance(
                raw_opening_depth, (int, float)
            ):
                result = _clearance_result(
                    rule_id=RULE_CABINET_OPENING,
                    entity=entity,
                    footprint=footprint,
                    outward=(-footprint.axis_v[0], -footprint.axis_v[1]),
                    lateral=lateral,
                    lateral_half=footprint.half_u,
                    required_mm=float(raw_opening_depth),
                    named_blockers=blockers,
                    explanation_available=(
                        "{entity_id} has {available:.0f} mm of the required "
                        "{required:.0f} mm door-opening clearance in front"
                    ),
                    suggestion_template="освободите минимум {deficit} мм перед фасадом",
                )
                if result is not None:
                    _emit(results, result)

    results.sort(key=_sort_key)
    summary = ValidationSummary(
        info=sum(1 for item in results if item.severity is CheckSeverity.INFO),
        warning=sum(1 for item in results if item.severity is CheckSeverity.WARNING),
        error=sum(1 for item in results if item.severity is CheckSeverity.ERROR),
    )
    return ValidationReport(
        scene_revision_id=scene_revision_id,
        scene_content_hash=scene_content_hash(scene),
        config=active,
        summary=summary,
        results=results,
    )


def _entity_metadata(scene: Scene, entity_id: str) -> dict[str, Any]:
    for entity in scene.entities:
        if entity.id == entity_id:
            return entity.metadata
    return {}


def _category(scene: Scene, entity: SceneEntity) -> str:
    """Lowercased category haystack for an entity (scene arg kept for symmetry
    with the other rule helpers; matching itself is per-entity)."""
    del scene
    return _entity_category_text(entity)


def _entity_category_text(entity: SceneEntity) -> str:
    parts = [entity.kind.value, entity.id, entity.display_name or ""]
    for key, value in entity.metadata.items():
        parts.append(str(key))
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
    return " ".join(parts).lower()
