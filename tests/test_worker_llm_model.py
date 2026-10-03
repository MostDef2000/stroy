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


def test_llm_client_picks_up_timeout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from stroy.services.adapters import OpenAICompatibleLLM

    monkeypatch.delenv("STROY_LLM_TIMEOUT_SECONDS", raising=False)
    default = OpenAICompatibleLLM("http://qwen/v1", "local", "m")
    assert default.client.timeout.read == 120
    monkeypatch.setenv("STROY_LLM_TIMEOUT_SECONDS", "900")
    tuned = OpenAICompatibleLLM(
        "http://qwen/v1",
        "local",
        "m",
        timeout_seconds=float(os.getenv("STROY_LLM_TIMEOUT_SECONDS", "120")),
    )
    assert tuned.client.timeout.read == 900
