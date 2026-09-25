from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class PaletteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hex: str
    role: str | None = None

    @field_validator("hex")
    @classmethod
    def normalize_hex(cls, value: str) -> str:
        if not _HEX.fullmatch(value):
            raise ValueError("palette hex must be #RRGGBB")
        return value.upper()


class MaterialStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    finish: str | None = None
    application: str | None = None


class LightingStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature_k: int | None = Field(default=None, ge=1000, le=12000)
    intent: list[str] = Field(default_factory=list)


class FormStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list)


class StyleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = "0.1.0"
    style_profile_id: str = Field(min_length=1)
    source_asset_ids: list[str] = Field(default_factory=list)
    source_text: str | None = None
    labels: list[str] = Field(default_factory=list)
    palette: list[PaletteEntry] = Field(default_factory=list)
    materials: list[MaterialStyle] = Field(default_factory=list)
    lighting: LightingStyle = Field(default_factory=LightingStyle)
    forms: FormStyle = Field(default_factory=FormStyle)
    negative_constraints: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StyleProfileProposal(BaseModel):
    """Model-produced style facts before deterministic user overrides."""

    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(default_factory=list)
    palette: list[PaletteEntry] = Field(default_factory=list)
    materials: list[MaterialStyle] = Field(default_factory=list)
    lighting: LightingStyle = Field(default_factory=LightingStyle)
    forms: FormStyle = Field(default_factory=FormStyle)
    negative_constraints: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class StyleOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[str] | None = None
    palette: list[PaletteEntry] | None = None
    materials: list[MaterialStyle] | None = None
    lighting: LightingStyle | None = None
    forms: FormStyle | None = None
    negative_constraints: list[str] | None = None


def merge_style_overrides(
    proposal: StyleProfileProposal,
    overrides: StyleOverrides | None,
) -> StyleProfileProposal:
    if overrides is None:
        return proposal
    data = proposal.model_dump(mode="python")
    for field_name in (
        "labels",
        "palette",
        "materials",
        "lighting",
        "forms",
        "negative_constraints",
    ):
        value = getattr(overrides, field_name)
        if value is not None:
            if isinstance(value, BaseModel):
                data[field_name] = value.model_dump(mode="python")
            else:
                data[field_name] = [
                    item.model_dump(mode="python") if isinstance(item, BaseModel) else item
                    for item in value
                ] if isinstance(value, list) else value
    return StyleProfileProposal.model_validate(data)
