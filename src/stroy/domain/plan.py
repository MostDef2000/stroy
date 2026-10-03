"""Structured plan draft produced from an upload of a bare-apartment plan.

A ``PlanDraft`` is the AI proposal that the owner corrects before it is
committed into a canonical scene revision.  It is intentionally independent
from ``Scene``: plan coordinates are 2D, scale may be unknown, and every
entity carries the raw identifier the model proposed so the web editor can
round-trip corrections.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanScaleSource(StrEnum):
    PLAN_LABEL = "plan_label"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class PlanScale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: PlanScaleSource
    mm_per_px: float | None = None

    @model_validator(mode="after")
    def validate_scale_consistency(self) -> "PlanScale":
        if self.source is PlanScaleSource.UNKNOWN:
            if self.mm_per_px is not None:
                raise ValueError(
                    "scale.mm_per_px must be null when scale.source is 'unknown'"
                )
        elif self.mm_per_px is None or self.mm_per_px <= 0:
            raise ValueError(
                "scale.mm_per_px must be a positive number when scale.source is known"
            )
        return self


class PlanOpening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["door", "window", "arch"]
    t: float = Field(ge=0.0, le=1.0)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    sill_mm: float | None = Field(default=None, ge=0)


class PlanWall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    x1: float
    y1: float
    x2: float
    y2: float
    thickness_mm: float = Field(gt=0)
    openings: list[PlanOpening] = Field(default_factory=list)

    @property
    def length_mm(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @model_validator(mode="after")
    def validate_length_covers_thickness(self) -> "PlanWall":
        if self.length_mm < self.thickness_mm:
            raise ValueError(
                f"wall {self.id!r} length {self.length_mm:.3f} is smaller than "
                f"its thickness {self.thickness_mm:.3f}"
            )
        return self

    @model_validator(mode="after")
    def validate_openings(self) -> "PlanWall":
        opening_ids = [opening.id for opening in self.openings]
        if len(opening_ids) != len(set(opening_ids)):
            raise ValueError(f"wall {self.id!r} has duplicate opening ids")
        for opening in self.openings:
            if opening.width_mm > self.length_mm:
                raise ValueError(
                    f"opening {opening.id!r} width {opening.width_mm:.3f} exceeds "
                    f"wall {self.id!r} length {self.length_mm:.3f}"
                )
        return self


class PlanRoom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    # Ordered loop of wall ids; the whole loop is corrected by the owner.
    wall_ids: list[str] = Field(min_length=1)
    floor_finish: str | None = None

    @model_validator(mode="after")
    def validate_wall_loop(self) -> "PlanRoom":
        if len(self.wall_ids) < 3:
            raise ValueError(
                f"room {self.id!r} must reference at least 3 walls to form a loop"
            )
        if len(self.wall_ids) != len(set(self.wall_ids)):
            raise ValueError(f"room {self.id!r} has duplicate wall ids")
        return self


class PlanFloor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="main", min_length=1)
    level_mm: float = 0.0
    walls: list[PlanWall] = Field(default_factory=list)
    rooms: list[PlanRoom] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_room_wall_references(self) -> "PlanFloor":
        wall_ids = [wall.id for wall in self.walls]
        if len(wall_ids) != len(set(wall_ids)):
            raise ValueError("wall ids must be unique within a floor")
        room_ids = [room.id for room in self.rooms]
        if len(room_ids) != len(set(room_ids)):
            raise ValueError("room ids must be unique within a floor")
        opening_ids = [
            opening.id for wall in self.walls for opening in wall.openings
        ]
        if len(opening_ids) != len(set(opening_ids)):
            raise ValueError("opening ids must be unique within a floor")
        known = set(wall_ids)
        for room in self.rooms:
            missing = [wall_id for wall_id in room.wall_ids if wall_id not in known]
            if missing:
                raise ValueError(
                    f"room {room.id!r} references unknown walls: {missing}"
                )
        return self


class PlanDraft(BaseModel):
    """Versioned, owner-correctable plan proposal.  Phase A uses one floor."""

    model_config = ConfigDict(extra="forbid")

    version: str = "0.1.0"
    units: Literal["mm"] = "mm"
    scale: PlanScale
    floors: list[PlanFloor] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_global_entity_ids(self) -> "PlanDraft":
        seen: set[str] = set()
        for floor in self.floors:
            entity_ids: list[str] = []
            for wall in floor.walls:
                entity_ids.append(wall.id)
                entity_ids.extend(opening.id for opening in wall.openings)
            entity_ids.extend(room.id for room in floor.rooms)
            for entity_id in entity_ids:
                if entity_id in seen:
                    raise ValueError(
                        f"entity id {entity_id!r} is used by more than one floor"
                    )
                seen.add(entity_id)
        return self


__all__ = [
    "PlanDraft",
    "PlanFloor",
    "PlanOpening",
    "PlanRoom",
    "PlanScale",
    "PlanScaleSource",
    "PlanWall",
]
