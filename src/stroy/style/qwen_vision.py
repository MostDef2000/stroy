"""Real vision style adapter backed by an OpenAI-compatible multimodal LLM.

Designed for ollama serving ``qwen3-vl`` (OpenAI-compatible
``/v1/chat/completions`` with base64 ``image_url`` parts) but works with any
endpoint that accepts the same message shape.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any, Protocol

from stroy.services.adapters import AdapterProtocolError, OpenAICompatibleLLM
from stroy.style.models import StyleProfileProposal


class MultimodalLLMClient(Protocol):
    """Duck-typed subset of the OpenAI-compatible LLM client."""

    model: str
    profile_id: str | None

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...

_DECODER = json.JSONDecoder()

_PROMPT_TEMPLATE = """You are an interior style analyst. Analyze the attached reference photo(s){source_clause} and reply with a single JSON object and nothing else.

Use exactly these fields:
{{
  "labels": ["2-4 short lowercase style labels"],
  "palette": [{{"hex": "#RRGGBB", "role": "base|accent|secondary"}}],
  "materials": [{{"name": "material name", "finish": "matte|satin|glossy|raw|textured", "application": "flooring|walls|furniture|upholstery"}}],
  "lighting": {{"temperature_k": 1000-12000, "intent": ["e.g. warm, diffused"]}},
  "forms": {{"keywords": ["short form descriptors"]}},
  "negative_constraints": ["styles/materials to avoid"],
  "evidence": {{}}
}}

Rules:
- hex colors must be #RRGGBB
- temperature_k must be an integer between 1000 and 12000
- keep lists short and concrete; no prose outside the JSON"""


def _data_url(image: bytes) -> str:
    if image.startswith(b"\x89PNG"):
        media_type = "image/png"
    elif image.startswith(b"\xff\xd8"):
        media_type = "image/jpeg"
    else:
        raise AdapterProtocolError("vision adapter supports only PNG and JPEG images")
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _build_messages(
    images: list[bytes],
    source_text: str | None,
    correction: str | None = None,
) -> list[dict[str, Any]]:
    source_clause = (
        f" together with the client brief: {source_text!r}" if source_text else ""
    )
    prompt = _PROMPT_TEMPLATE.format(source_clause=source_clause)
    if correction:
        prompt = f"{prompt}\n\nCorrection: {correction}"
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        content.append({"type": "image_url", "image_url": {"url": _data_url(image)}})
    return [
        {"role": "system", "content": "You reply with strict JSON only."},
        {"role": "user", "content": content},
    ]


def _extract_proposal(content: str) -> StyleProfileProposal:
    # raw_decode instead of a greedy regex+loads: the model sometimes appends
    # prose after the JSON object ("Extra data" would break json.loads).
    start = content.find("{")
    if start == -1:
        raise ValueError("no JSON object found in the model reply")
    raw, _end = _DECODER.raw_decode(content[start:])
    return StyleProfileProposal.model_validate(raw)


class QwenVisionStyleAdapter:
    """Ask a multimodal LLM for a StyleProfileProposal and validate the reply."""

    def __init__(
        self,
        llm: MultimodalLLMClient,
        *,
        model_profile: str | None = None,
        attempts: int = 2,
    ) -> None:
        self.llm = llm
        self.model_profile = model_profile or llm.profile_id or llm.model
        self.attempts = max(1, attempts)

    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": "qwen-vision",
            "model_profile": self.model_profile,
            "model": self.llm.model,
        }

    async def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.llm.complete(messages)

    @staticmethod
    def _reply_content(response: dict[str, Any]) -> str:
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterProtocolError("malformed LLM response") from exc
        if not isinstance(content, str):
            raise AdapterProtocolError("LLM reply is not a text message")
        return content

    async def analyze_style(
        self,
        *,
        images: list[bytes],
        source_text: str | None = None,
        input_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if not images:
            raise AdapterProtocolError("Vision analysis requires at least one image")

        messages = _build_messages(images, source_text)
        correction: str | None = None
        last_error: Exception | None = None
        for _attempt in range(self.attempts):
            if correction is not None:
                messages = _build_messages(images, source_text, correction)
            try:
                reply = await self._complete(messages)
                proposal = _extract_proposal(self._reply_content(reply))
                return {
                    "style_profile": proposal.model_dump(mode="json"),
                    "adapter_provenance": self.provenance(),
                }
            except (ValueError, binascii.Error, AdapterProtocolError) as exc:
                last_error = exc
                correction = (
                    f"your previous answer was rejected ({exc}). "
                    "Return ONE valid JSON object only, no markdown fences."
                )
        raise AdapterProtocolError(
            f"vision model did not return a valid style proposal: {last_error}"
        )


def build_local_vision_adapter() -> QwenVisionStyleAdapter:
    """Build the real vision adapter from environment configuration."""
    model = os.getenv("STROY_LLM_MODEL")
    if not model:
        raise ValueError(
            "STROY_LLM_MODEL is required when STROY_STYLE_VISION_ADAPTER=local"
        )
    llm = OpenAICompatibleLLM(
        os.getenv("STROY_LLM_BASE_URL", "http://127.0.0.1:8001/v1"),
        os.getenv("STROY_LLM_API_KEY", "local"),
        model,
        profile_id=os.getenv("STROY_LLM_MODEL_PROFILE"),
        timeout_seconds=float(os.getenv("STROY_LLM_TIMEOUT_SECONDS", "120")),
    )
    return QwenVisionStyleAdapter(llm)
