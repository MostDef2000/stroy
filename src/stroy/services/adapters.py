from __future__ import annotations

import asyncio
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
                return result
            await asyncio.sleep(poll_seconds)
        raise AdapterTimeout(f"ComfyUI prompt timed out: {prompt_id}")

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
