from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from stroy.domain.plan import PlanDraft


ROOT = Path(__file__).resolve().parents[1]


def schema() -> dict:
    return json.loads(
        (ROOT / "schemas" / "plan-draft.schema.json").read_text(encoding="utf-8")
    )


def wall(
    wall_id: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    thickness: float = 150.0,
    openings: list[dict] | None = None,
) -> dict:
    return {
        "id": wall_id,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "thickness_mm": thickness,
        "openings": openings or [],
    }


def two_room_draft(*, scale: dict | None = None) -> dict:
    return {
        "version": "0.1.0",
        "units": "mm",
        "scale": scale or {"source": "manual", "mm_per_px": 1.0},
        "floors": [
            {
                "name": "main",
                "level_mm": 0.0,
                "walls": [
                    wall("wall.1", 0, 0, 4000, 0),
                    wall("wall.2", 4000, 0, 4000, 3000),
                    wall(
                        "wall.3",
                        4000,
                        3000,
                        0,
                        3000,
                        openings=[
                            {
                                "id": "opening.window.1",
                                "kind": "window",
                                "t": 0.5,
                                "width_mm": 1600,
                                "height_mm": 1200,
                                "sill_mm": 900,
                            }
                        ],
                    ),
                    wall("wall.4", 0, 3000, 0, 0),
                    wall("wall.5", 5000, 0, 8000, 0),
                    wall("wall.6", 8000, 0, 8000, 3000),
                    wall("wall.7", 8000, 3000, 5000, 3000),
                    wall(
                        "wall.8",
                        5000,
                        3000,
                        5000,
                        0,
                        openings=[
                            {
                                "id": "opening.door.1",
                                "kind": "door",
                                "t": 0.25,
                                "width_mm": 900,
                                "height_mm": 2100,
                                "sill_mm": 0,
                            }
                        ],
                    ),
                ],
                "rooms": [
                    {
                        "id": "room.living",
                        "name": "Living room",
                        "wall_ids": ["wall.1", "wall.2", "wall.3", "wall.4"],
                        "floor_finish": None,
                    },
                    {
                        "id": "room.bedroom",
                        "name": "Bedroom",
                        "wall_ids": ["wall.5", "wall.6", "wall.7", "wall.8"],
                        "floor_finish": None,
                    },
                ],
            }
        ],
    }


def test_valid_two_room_draft_matches_schema() -> None:
    draft = PlanDraft.model_validate(two_room_draft())
    jsonschema.validate(
        draft.model_dump(mode="json", exclude_none=True),
        schema(),
    )
    assert len(draft.floors[0].rooms) == 2


def test_opening_t_out_of_range_is_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["walls"][0]["openings"] = [
        {
            "id": "opening.door.bad",
            "kind": "door",
            "t": 1.5,
            "width_mm": 900,
            "height_mm": 2100,
            "sill_mm": 0,
        }
    ]
    with pytest.raises(ValidationError):
        PlanDraft.model_validate(raw)


def test_room_referencing_unknown_wall_is_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["rooms"][0]["wall_ids"] = ["wall.1", "wall.2", "wall.missing"]
    with pytest.raises(ValidationError, match="unknown walls"):
        PlanDraft.model_validate(raw)


def test_unknown_scale_requires_null_mm_per_px() -> None:
    with pytest.raises(ValidationError, match="mm_per_px must be null"):
        PlanDraft.model_validate(
            two_room_draft(scale={"source": "unknown", "mm_per_px": 10.0})
        )

    draft = PlanDraft.model_validate(two_room_draft(scale={"source": "unknown"}))
    assert draft.scale.source.value == "unknown"
    assert draft.scale.mm_per_px is None


def test_known_scale_requires_positive_mm_per_px() -> None:
    with pytest.raises(ValidationError, match="positive number"):
        PlanDraft.model_validate(two_room_draft(scale={"source": "plan_label"}))


def test_wall_shorter_than_thickness_is_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["walls"][0] = wall("wall.1", 0, 0, 10, 0, thickness=100.0)
    with pytest.raises(ValidationError, match="smaller than"):
        PlanDraft.model_validate(raw)


def test_duplicate_opening_ids_within_wall_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["walls"][0]["openings"] = [
        {"id": "opening.dup", "kind": "door", "t": 0.3,
         "width_mm": 900, "height_mm": 2100, "sill_mm": 0},
        {"id": "opening.dup", "kind": "window", "t": 0.7,
         "width_mm": 900, "height_mm": 1200, "sill_mm": 600},
    ]
    with pytest.raises(ValidationError, match="duplicate opening ids"):
        PlanDraft.model_validate(raw)


def test_duplicate_opening_ids_across_walls_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["walls"][0]["openings"] = [
        {"id": "opening.dup", "kind": "door", "t": 0.5,
         "width_mm": 900, "height_mm": 2100, "sill_mm": 0},
    ]
    raw["floors"][0]["walls"][2]["openings"] = [
        {"id": "opening.dup", "kind": "window", "t": 0.5,
         "width_mm": 900, "height_mm": 1200, "sill_mm": 600},
    ]
    with pytest.raises(ValidationError, match="unique within a floor"):
        PlanDraft.model_validate(raw)


def test_opening_wider_than_host_wall_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["walls"][0]["openings"] = [
        {"id": "opening.door.wide", "kind": "door", "t": 0.5,
         "width_mm": 5000, "height_mm": 2100, "sill_mm": 0},
    ]
    with pytest.raises(ValidationError, match="exceeds"):
        PlanDraft.model_validate(raw)


def test_room_with_fewer_than_three_walls_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["rooms"][0]["wall_ids"] = ["wall.1", "wall.2"]
    with pytest.raises(ValidationError, match="at least 3 walls"):
        PlanDraft.model_validate(raw)


def test_room_with_duplicate_wall_ids_rejected() -> None:
    raw = two_room_draft()
    raw["floors"][0]["rooms"][0]["wall_ids"] = [
        "wall.1",
        "wall.2",
        "wall.2",
        "wall.1",
    ]
    with pytest.raises(ValidationError, match="duplicate wall ids"):
        PlanDraft.model_validate(raw)


def test_cross_floor_entity_id_collision_rejected() -> None:
    raw = two_room_draft()
    upper = json.loads(json.dumps(raw["floors"][0]))
    upper["name"] = "upper"
    raw["floors"].append(upper)
    with pytest.raises(ValidationError, match="more than one floor"):
        PlanDraft.model_validate(raw)
