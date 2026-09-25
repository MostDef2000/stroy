from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LicenseProfile(BaseModel):
    name: str
    status: Literal["permissive", "restricted", "review-required"]
    source: str | None = None
    reviewed_on: str | None = None


class ModelProfile(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["llm", "image"]
    upstream: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    license: LicenseProfile
    approved_uses: list[str]
    notes: str | None = None


class ModelProfileRegistry:
    def __init__(self, profiles: list[ModelProfile]) -> None:
        self._profiles = {profile.id: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("model profile IDs must be unique")

    @classmethod
    def load(cls, path: str | Path) -> "ModelProfileRegistry":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("model profile file must contain a JSON array")
        return cls([ModelProfile.model_validate(item) for item in payload])

    def get(
        self,
        profile_id: str,
        *,
        kind: Literal["llm", "image"] | None = None,
        approved_use: str | None = None,
    ) -> ModelProfile:
        try:
            profile = self._profiles[profile_id]
        except KeyError as exc:
            raise ValueError(f"unknown model profile: {profile_id}") from exc
        if kind is not None and profile.kind != kind:
            raise ValueError(
                f"model profile {profile_id} has kind {profile.kind}, expected {kind}"
            )
        if approved_use is not None and approved_use not in profile.approved_uses:
            raise ValueError(
                f"model profile {profile_id} is not approved for {approved_use}"
            )
        return profile

    def list(self, *, kind: Literal["llm", "image"] | None = None) -> list[ModelProfile]:
        profiles = list(self._profiles.values())
        if kind is not None:
            profiles = [profile for profile in profiles if profile.kind == kind]
        return sorted(profiles, key=lambda profile: profile.id)
