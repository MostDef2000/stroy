from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
from typing import Any

import httpx


class AdapterError(RuntimeError):
    code = "adapter_error"
    retryable = False

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def as_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "context": {"retryable": self.retryable},
        }


class AdapterTimeout(AdapterError):
    code = "adapter_timeout"
    retryable = True


class AdapterUnavailable(AdapterError):
    code = "adapter_unavailable"
    retryable = True


class AdapterProtocolError(AdapterError):
    code = "adapter_protocol_error"
    retryable = False


class AdapterExecutionError(AdapterError):
    code = "runtime_execution_error"
    retryable = False


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise AdapterTimeout(f"request timed out: {url}") from exc
    except httpx.RequestError as exc:
        raise AdapterUnavailable(f"request failed: {url}: {exc}") from exc

    if response.status_code >= 500:
        raise AdapterUnavailable(
            f"runtime returned HTTP {response.status_code}: {url}"
        )
    if response.status_code >= 400:
        raise AdapterProtocolError(
            f"runtime rejected request with HTTP {response.status_code}: {url}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise AdapterProtocolError(f"runtime returned invalid JSON: {url}") from exc
    if not isinstance(payload, dict):
        raise AdapterProtocolError(f"runtime JSON root must be an object: {url}")
    return payload


async def _request_bytes(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> bytes:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise AdapterTimeout(f"request timed out: {url}") from exc
    except httpx.RequestError as exc:
        raise AdapterUnavailable(f"request failed: {url}: {exc}") from exc

    if response.status_code >= 500:
        raise AdapterUnavailable(
            f"runtime returned HTTP {response.status_code}: {url}"
        )
    if response.status_code >= 400:
        raise AdapterProtocolError(
            f"runtime rejected request with HTTP {response.status_code}: {url}"
        )
    return response.content


class OpenAICompatibleLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        profile_id: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.profile_id = profile_id
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": "openai-compatible",
            "model_profile": self.profile_id,
            "model": self.model,
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return await _request_json(
            self.client,
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )


class ComfyUIAdapter:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def provenance(self) -> dict[str, Any]:
        return {"adapter": "comfyui"}

    async def submit(self, workflow: dict[str, Any], client_id: str) -> str:
        data = await _request_json(
            self.client,
            "POST",
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
        )
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise AdapterProtocolError("ComfyUI response is missing prompt_id")
        return prompt_id

    async def wait(
        self,
        prompt_id: str,
        *,
        poll_seconds: float = 1.0,
        timeout_seconds: int = 900,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            history = await _request_json(
                self.client,
                "GET",
                f"{self.base_url}/history/{prompt_id}",
            )
            if prompt_id in history:
                result = history[prompt_id]
                if not isinstance(result, dict):
                    raise AdapterProtocolError(
                        "ComfyUI history entry must be an object"
                    )
                status = result.get("status") or {}
                if status and not isinstance(status, dict):
                    raise AdapterProtocolError(
                        "ComfyUI history status must be an object"
                    )
                status_str = str(status.get("status_str", "")).lower()
                messages = status.get("messages") or []
                has_execution_error = any(
                    isinstance(message, (list, tuple))
                    and bool(message)
                    and message[0] == "execution_error"
                    for message in messages
                )
                if status_str in {"error", "failed"} or has_execution_error:
                    raise AdapterExecutionError(
                        f"ComfyUI prompt failed: {prompt_id}"
                    )
                if (
                    status.get("completed") is True
                    or status_str == "success"
                    or bool(result.get("outputs"))
                ):
                    return result
            await asyncio.sleep(poll_seconds)
        raise AdapterTimeout(f"ComfyUI prompt timed out: {prompt_id}")

    async def cancel(self, prompt_id: str) -> bool:
        payload = await _request_json(
            self.client,
            "POST",
            f"{self.base_url}/api/jobs/{prompt_id}/cancel",
        )
        cancelled = payload.get("cancelled")
        if not isinstance(cancelled, bool):
            raise AdapterProtocolError(
                "ComfyUI cancel response is missing boolean cancelled"
            )
        return cancelled

    async def collect_output_images(
        self,
        history: dict[str, Any],
    ) -> list[dict[str, Any]]:
        outputs = history.get("outputs") or {}
        if not isinstance(outputs, dict):
            raise AdapterProtocolError("ComfyUI history.outputs must be an object")

        artifacts: list[dict[str, Any]] = []
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            images = node_output.get("images") or []
            if not isinstance(images, list):
                raise AdapterProtocolError("ComfyUI output images must be a list")
            for descriptor in images:
                if not isinstance(descriptor, dict):
                    raise AdapterProtocolError(
                        "ComfyUI image descriptor must be an object"
                    )
                filename = descriptor.get("filename")
                if not isinstance(filename, str) or not filename:
                    raise AdapterProtocolError(
                        "ComfyUI image descriptor is missing filename"
                    )
                subfolder = descriptor.get("subfolder") or ""
                image_type = descriptor.get("type") or "output"
                data = await _request_bytes(
                    self.client,
                    "GET",
                    f"{self.base_url}/view",
                    params={
                        "filename": filename,
                        "subfolder": subfolder,
                        "type": image_type,
                    },
                )
                safe_name = filename.replace("\\", "/").split("/")[-1]
                media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
                artifacts.append(
                    {
                        "filename": safe_name,
                        "media_type": media_type,
                        "data": data,
                    }
                )
        return artifacts


class FakeLLMAdapter:
    """Deterministic adapter for E2E tests before Qwen is connected."""

    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": "fake-llm",
            "model_profile": "fake",
            "model": "fake",
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = " ".join(str(message.get("content", "")) for message in messages).lower()
        calls: list[dict[str, Any]] = []
        if ("диван" in text or "sofa" in text) and ("беж" in text or "beige" in text):
            calls.append(
                {
                    "name": "set_color",
                    "arguments": {
                        "target_id": "object.sofa.main",
                        "color": "#D7C4AB",
                    },
                }
            )
        if ("дерев" in text or "wood" in text) and (
            "светл" in text or "lighten" in text or "lighter" in text
        ):
            calls.append(
                {
                    "name": "set_material",
                    "arguments": {
                        "target_id": "object.sofa.main",
                        "material_ref": "material.wood.light-oak",
                    },
                }
            )
        if ("убери" in text or "remove" in text) and (
            "стол" in text or "table" in text
        ):
            calls.append(
                {
                    "name": "remove_object",
                    "arguments": {"target_id": "object.coffee_table.main"},
                }
            )
        return {"fake": True, "tool_calls": calls}



class OpenAICompatibleVisionStyle:
    """Pluggable multimodal style analyzer for an OpenAI-compatible local runtime."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": "openai-compatible-vision",
            "model": self.model,
        }

    async def analyze(
        self,
        source_text: str,
        images: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not 3 <= len(images) <= 5:
            raise AdapterProtocolError("style analysis requires 3 to 5 reference images")

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Analyze these interior references and the user instruction. "
                    "Return one JSON object with key style_profile. "
                    "style_profile must contain labels, palette, materials, lighting, "
                    "forms, negative_constraints and evidence. Palette colors must be "
                    "#RRGGBB. Do not return markdown.\n\nUser instruction:\n"
                    + source_text
                ),
            }
        ]
        for image in images:
            data = image.get("data")
            media_type = image.get("media_type")
            if not isinstance(data, bytes) or not isinstance(media_type, str):
                raise AdapterProtocolError("vision image requires bytes and media_type")
            encoded = base64.b64encode(data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{encoded}",
                    },
                }
            )

        raw = await _request_json(
            self.client,
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You extract structured interior-design style facts.",
                    },
                    {"role": "user", "content": content},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
        )
        choices = raw.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise AdapterProtocolError("vision response is missing choices")
        message = choices[0].get("message") or {}
        raw_content = message.get("content")
        if isinstance(raw_content, dict):
            parsed = raw_content
        elif isinstance(raw_content, str):
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                raise AdapterProtocolError("vision response content is not valid JSON") from exc
        else:
            raise AdapterProtocolError("vision response content is missing")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("style_profile"), dict):
            raise AdapterProtocolError("vision response requires style_profile object")
        return {
            "style_profile": parsed["style_profile"],
            "adapter_provenance": self.provenance(),
        }


class FakeVisionStyleAdapter:
    """Deterministic multimodal fixture adapter that proves reference bytes were consumed."""

    def provenance(self) -> dict[str, Any]:
        return {"adapter": "fake-vision-style", "model": "fake-vision"}

    async def analyze(
        self,
        source_text: str,
        images: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not 3 <= len(images) <= 5:
            raise AdapterProtocolError("style analysis requires 3 to 5 reference images")
        lowered = source_text.lower()
        digests = []
        for image in images:
            data = image.get("data")
            if not isinstance(data, bytes):
                raise AdapterProtocolError("fake vision image requires bytes")
            digests.append(hashlib.sha256(data).hexdigest())

        warm = any(token in lowered for token in ("warm", "тепл", "уют"))
        minimal = any(token in lowered for token in ("minimal", "миним"))
        wood = any(token in lowered for token in ("wood", "дерев"))
        return {
            "style_profile": {
                "labels": ["minimal"] if minimal else ["contemporary"],
                "palette": [
                    {"hex": "#D8D0C4", "role": "base"},
                    {"hex": "#8A8178", "role": "accent"},
                ],
                "materials": [
                    {
                        "name": "natural wood" if wood else "matte plaster",
                        "finish": "matte",
                        "application": "primary surfaces",
                    }
                ],
                "lighting": {
                    "temperature_k": 3000 if warm else 3500,
                    "intent": ["soft", "ambient"],
                },
                "forms": {
                    "keywords": (
                        ["clean lines", "rounded accents"]
                        if minimal
                        else ["balanced proportions"]
                    )
                },
                "negative_constraints": [],
                "evidence": {
                    "mode": "fake-vision",
                    "image_sha256": digests,
                    "image_count": len(images),
                },
            },
            "adapter_provenance": self.provenance(),
        }
