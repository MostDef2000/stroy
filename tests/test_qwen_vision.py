from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from stroy.services.adapters import AdapterProtocolError
from stroy.style.qwen_vision import (
    QwenVisionStyleAdapter,
    _build_messages,
)
from stroy.worker.executors import VisionStyleExecutor, build_style_analyze_executor

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_VALID_PROPOSAL = {
    "labels": ["minimal", "scandinavian"],
    "palette": [{"hex": "#F5F5F5", "role": "base"}, {"hex": "#A0A0A0"}],
    "materials": [{"name": "light ash wood", "finish": "matte", "application": "flooring"}],
    "lighting": {"temperature_k": 4000, "intent": ["bright", "diffused"]},
    "forms": {"keywords": ["clean lines"]},
    "negative_constraints": ["heavy drapery"],
    "evidence": {},
}


class StubLLM:
    def __init__(self, replies: list[str], model: str = "qwen3-vl:8b") -> None:
        self.replies = list(replies)
        self.model = model
        self.profile_id: str | None = "qwen3-vl-8b"
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.replies.pop(0)}}]}


def _adapter(replies: list[str]) -> tuple[QwenVisionStyleAdapter, StubLLM]:
    stub = StubLLM(replies)
    return QwenVisionStyleAdapter(stub, attempts=2), stub


async def test_valid_reply_parses_into_proposal() -> None:
    adapter, stub = _adapter([json.dumps(_VALID_PROPOSAL)])
    result = await adapter.analyze_style(images=[_PNG])
    proposal = result["style_profile"]
    assert proposal["labels"] == ["minimal", "scandinavian"]
    assert proposal["palette"][0]["hex"] == "#F5F5F5"
    assert result["adapter_provenance"] == {
        "adapter": "qwen-vision",
        "model_profile": "qwen3-vl-8b",
        "model": "qwen3-vl:8b",
    }
    assert stub.calls[0][0]["role"] == "system"
    user_content = stub.calls[0][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "skan" not in user_content[0]["text"]  # no brief -> no source clause


async def test_multimodal_request_carries_base64_image_parts() -> None:
    adapter, stub = _adapter([json.dumps(_VALID_PROPOSAL)])
    await adapter.analyze_style(images=[_PNG, _JPEG], source_text="тёплая классика")
    user_content = stub.calls[0][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "тёплая классика" in user_content[0]["text"]
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user_content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_invalid_json_retries_then_succeeds() -> None:
    adapter, stub = _adapter(["here is the style: <not json>", json.dumps(_VALID_PROPOSAL)])
    result = await adapter.analyze_style(images=[_PNG])
    assert result["style_profile"]["labels"]
    assert len(stub.calls) == 2
    assert "rejected" in stub.calls[1][1]["content"][0]["text"]


async def test_persistently_invalid_json_raises_protocol_error() -> None:
    adapter, stub = _adapter(["nope", "still nope"])
    try:
        await adapter.analyze_style(images=[_PNG])
    except AdapterProtocolError as exc:
        assert "did not return a valid style proposal" in str(exc)
    else:
        raise AssertionError("AdapterProtocolError expected")
    assert len(stub.calls) == 2


async def test_empty_images_rejected_before_llm_call() -> None:
    adapter, stub = _adapter([])
    try:
        await adapter.analyze_style(images=[])
    except AdapterProtocolError as exc:
        assert "at least one image" in str(exc)
    else:
        raise AssertionError("AdapterProtocolError expected")
    assert stub.calls == []


async def test_unsupported_image_format_rejected() -> None:
    adapter, _ = _adapter([])
    try:
        await adapter.analyze_style(images=[b"GIF89a-not-supported"])
    except AdapterProtocolError as exc:
        assert "PNG and JPEG" in str(exc)
    else:
        raise AssertionError("AdapterProtocolError expected")


def test_build_messages_prompt_demands_strict_json() -> None:
    messages = _build_messages([_PNG], None)
    assert messages[0]["content"] == "You reply with strict JSON only."
    text = messages[1]["content"][0]["text"]
    assert "single JSON object" in text
    assert "#RRGGBB" in text


def test_factory_local_requires_model_env() -> None:
    try:
        build_style_analyze_executor("local")
    except ValueError as exc:
        assert "STROY_LLM_MODEL" in str(exc)
    else:
        raise AssertionError("ValueError expected without STROY_LLM_MODEL")


def test_factory_local_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STROY_LLM_MODEL", "qwen3-vl:8b")
    monkeypatch.setenv("STROY_LLM_MODEL_PROFILE", "qwen3-vl-8b")
    executor = build_style_analyze_executor("local")
    assert isinstance(executor, VisionStyleExecutor)
    assert executor.adapter.provenance()["model_profile"] == "qwen3-vl-8b"


def test_factory_unknown_adapter_still_rejected() -> None:
    try:
        build_style_analyze_executor("warp-drive")
    except ValueError as exc:
        assert "warp-drive" in str(exc)
    else:
        raise AssertionError("ValueError expected")
