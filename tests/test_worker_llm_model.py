"""Worker local-mode LLM model resolution (env override over profile upstream)."""

import pytest

from stroy.worker.main import resolve_llm_model


def test_profile_upstream_used_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STROY_LLM_MODEL", raising=False)
    assert resolve_llm_model("Qwen/Qwen3-14B") == "Qwen/Qwen3-14B"


def test_env_overrides_profile_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STROY_LLM_MODEL", "qwen3-vl-16k")
    assert resolve_llm_model("Qwen/Qwen3-14B") == "qwen3-vl-16k"


def test_empty_env_falls_back_to_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STROY_LLM_MODEL", "")
    assert resolve_llm_model("Qwen/Qwen3-14B") == "Qwen/Qwen3-14B"
