#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_model_profiles() -> int:
    schema = load(ROOT / "schemas" / "model-profile.schema.json")
    profiles = load(ROOT / "config" / "model-profiles.json")
    if not isinstance(profiles, list) or not profiles:
        raise SystemExit("config/model-profiles.json must be a non-empty array")

    ids: set[str] = set()
    for profile in profiles:
        jsonschema.validate(profile, schema)
        profile_id = profile["id"]
        if profile_id in ids:
            raise SystemExit(f"duplicate model profile id: {profile_id}")
        ids.add(profile_id)

        license_meta = profile["license"]
        if not license_meta.get("source") or not license_meta.get("reviewed_on"):
            raise SystemExit(
                f"model profile {profile_id} must record license source and reviewed_on"
            )
        if "personal-non-commercial" not in profile["approved_uses"]:
            raise SystemExit(
                f"model profile {profile_id} must explicitly declare current use mode"
            )

    dev = next((item for item in profiles if item["id"] == "flux-dev-family"), None)
    if dev is None:
        raise SystemExit("flux-dev-family profile is required")
    if dev["license"]["status"] != "restricted":
        raise SystemExit("flux-dev-family must remain marked restricted")
    if "re-review" not in dev.get("notes", "").lower():
        raise SystemExit("flux-dev-family notes must require license re-review")

    print(f"OK: validated {len(profiles)} model profile(s) against JSON Schema")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate_model_profiles())
