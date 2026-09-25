from pathlib import Path

from jsonschema import validate

from stroy.style import (
    LightingStyle,
    PaletteEntry,
    StyleProfile,
    StyleProfileProposal,
    merge_style_overrides,
)
from stroy.style.models import StyleOverrides


ROOT = Path(__file__).resolve().parents[1]


def test_palette_is_normalized() -> None:
    entry = PaletteEntry(hex="#d8d0c4", role="base")
    assert entry.hex == "#D8D0C4"


def test_user_overrides_win_deterministically() -> None:
    proposal = StyleProfileProposal(
        labels=["industrial"],
        palette=[{"hex": "#111111", "role": "base"}],
        lighting=LightingStyle(temperature_k=4000, intent=["neutral"]),
    )
    merged = merge_style_overrides(
        proposal,
        StyleOverrides(
            labels=["warm minimal"],
            palette=[{"hex": "#E8E0D6", "role": "base"}],
            lighting={"temperature_k": 3000, "intent": ["soft", "ambient"]},
        ),
    )
    assert merged.labels == ["warm minimal"]
    assert merged.palette[0].hex == "#E8E0D6"
    assert merged.lighting.temperature_k == 3000


def test_style_profile_matches_json_schema() -> None:
    profile = StyleProfile(
        style_profile_id="style-1",
        source_asset_ids=["asset-1", "asset-2", "asset-3"],
        source_text="warm minimal interior",
        labels=["warm minimal"],
        palette=[{"hex": "#E8E0D6", "role": "base"}],
        materials=[
            {
                "name": "natural wood",
                "finish": "matte",
                "application": "cabinetry",
            }
        ],
        lighting={"temperature_k": 3000, "intent": ["soft"]},
        forms={"keywords": ["clean lines"]},
        negative_constraints=["glossy chrome"],
    )
    import json
    schema = json.loads(
        (ROOT / "schemas" / "style-profile.schema.json").read_text(encoding="utf-8")
    )
    validate(profile.model_dump(mode="json", exclude_none=True), schema)
