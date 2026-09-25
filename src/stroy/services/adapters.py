from __future__ import annotations

import asyncio
from typing import Any

import httpx


class OpenAICompatibleLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=120)

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
        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


class ComfyUIAdapter:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=120)

    async def submit(self, workflow: dict[str, Any], client_id: str) -> str:
        response = await self.client.post(
            f"{self.base_url}/prompt", json={"prompt": workflow, "client_id": client_id}
        )
        response.raise_for_status()
        return response.json()["prompt_id"]

    async def wait(
        self, prompt_id: str, *, poll_seconds: float = 1.0, timeout_seconds: int = 900
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            response = await self.client.get(f"{self.base_url}/history/{prompt_id}")
            response.raise_for_status()
            history = response.json()
            if prompt_id in history:
                return history[prompt_id]
            await asyncio.sleep(poll_seconds)
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")


class FakeLLMAdapter:
    """Deterministic adapter for E2E tests before Qwen is connected."""

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
                    "arguments": {"target_id": "object.sofa.main", "color": "#D7C4AB"},
                }
            )
        if ("убери" in text or "remove" in text) and ("стол" in text or "table" in text):
            calls.append(
                {"name": "remove_object", "arguments": {"target_id": "object.coffee_table.main"}}
            )
        return {"fake": True, "tool_calls": calls}
