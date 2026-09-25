from pathlib import Path

import pytest

from stroy.models import ModelProfileRegistry


ROOT = Path(__file__).resolve().parents[1]


def registry() -> ModelProfileRegistry:
    return ModelProfileRegistry.load(ROOT / "config" / "model-profiles.json")


def test_qwen_profile_is_selected_by_id_and_use() -> None:
    profile = registry().get(
        "qwen3-14b",
        kind="llm",
        approved_use="personal-non-commercial",
    )
    assert profile.upstream == "Qwen/Qwen3-14B"
    assert profile.runtime == "openai-compatible"


def test_profile_kind_mismatch_fails() -> None:
    with pytest.raises(ValueError, match="expected llm"):
        registry().get("flux1-schnell", kind="llm")


def test_profile_unapproved_use_fails() -> None:
    with pytest.raises(ValueError, match="not approved"):
        registry().get(
            "flux-dev-family",
            kind="image",
            approved_use="commercial-production",
        )


def test_profile_ids_are_unique() -> None:
    first = registry().get("qwen3-14b")
    with pytest.raises(ValueError, match="unique"):
        ModelProfileRegistry([first, first])
