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


class RecordingAdapter:
    """Captures the images an executor passes to the adapter."""

    def __init__(self) -> None:
        self.seen: list[list[bytes]] = []

    def provenance(self) -> dict[str, Any]:
        return {"adapter": "recording", "model_profile": "test"}

    async def analyze_style(
        self,
        *,
        images: list[bytes],
        source_text: str | None = None,
        input_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self.seen.append(images)
        return {"style_profile": {"labels": ["x"]}, "adapter_provenance": self.provenance()}


class StubDownloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.requested: list[str] = []

    async def download_input(self, url: str) -> bytes:
        self.requested.append(url)
        return self.payloads[url]


async def test_executor_downloads_real_asset_bytes() -> None:
    adapter = RecordingAdapter()
    downloader = StubDownloader(
        {
            "http://internal/asset-1": _PNG,
            "http://internal/asset-2": _JPEG,
        }
    )
    executor = VisionStyleExecutor(adapter, downloader)  # type: ignore[arg-type]
    job = {
        "job_id": "job-1",
        "payload": {"input_asset_ids": ["asset-1", "asset-2"]},
        "download_urls": {"asset-1": "http://internal/asset-1", "asset-2": "http://internal/asset-2"},
    }
    await executor.execute(job)
    assert downloader.requested == ["http://internal/asset-1", "http://internal/asset-2"]
    assert adapter.seen == [[_PNG, _JPEG]]


async def test_executor_download_failure_raises_value_error() -> None:
    class FailingDownloader:
        async def download_input(self, url: str) -> bytes:
            raise RuntimeError("connection reset")

    executor = VisionStyleExecutor(RecordingAdapter(), FailingDownloader())  # type: ignore[arg-type]
    job = {
        "job_id": "job-1",
        "payload": {"input_asset_ids": ["asset-1"]},
        "download_urls": {"asset-1": "http://internal/asset-1"},
    }
    try:
        await executor.execute(job)
    except ValueError as exc:
        assert "style job asset download failed" in str(exc)
    else:
        raise AssertionError("ValueError expected")


async def test_executor_without_downloader_keeps_deterministic_fallback() -> None:
    adapter = RecordingAdapter()
    executor = VisionStyleExecutor(adapter)
    job = {
        "job_id": "job-1",
        "payload": {"input_asset_ids": ["asset-9"]},
        "download_urls": {"asset-9": "http://internal/asset-9"},
    }
    await executor.execute(job)
    # no download client -> deterministic digest fallback, not a crash
    assert adapter.seen and len(adapter.seen[0]) == 1


async def test_parses_json_when_model_appends_trailing_text() -> None:
    """Live v4 finding: the model sometimes adds prose after the JSON object."""
    payload = json.dumps(_VALID_PROPOSAL)
    adapter, stub = _adapter([f"Here is the analysis:\n{payload}\nHope this helps!"])
    result = await adapter.analyze_style(images=[_PNG])
    assert result["style_profile"]["labels"] == ["minimal", "scandinavian"]
    assert len(stub.calls) == 1  # no retry needed
