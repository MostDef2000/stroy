from __future__ import annotations

import json
from typing import Any, Protocol

from stroy.services.adapters import ComfyUIAdapter


class LLMAdapter(Protocol):
    def provenance(self) -> dict[str, Any]: ...

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]: ...


def normalize_llm_result(raw: dict[str, Any]) -> dict[str, Any]:
    if "tool_calls" in raw and "choices" not in raw:
        return raw

    choices = raw.get("choices") or []
    if not choices:
        return {"content": None, "tool_calls": []}

    message = choices[0].get("message") or {}
    normalized_calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = function.get("name")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON tool arguments for {name}") from exc
        if not isinstance(arguments, dict):
            raise ValueError(f"tool arguments for {name} must be an object")
        normalized_calls.append({"name": name, "arguments": arguments})

    return {
        "content": message.get("content"),
        "tool_calls": normalized_calls,
    }


class QwenExecutor:
    def __init__(self, adapter: LLMAdapter) -> None:
        self.adapter = adapter

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        raw = await self.adapter.complete(
            payload.get("messages", []),
            tools=payload.get("tools"),
        )
        result = normalize_llm_result(raw)
        result["adapter_provenance"] = self.adapter.provenance()
        return result


class ComfyUIExecutor:
    def __init__(self, adapter: ComfyUIAdapter, worker_id: str) -> None:
        self.adapter = adapter
        self.worker_id = worker_id

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise ValueError("image job requires payload.workflow")
        prompt_id = await self.adapter.submit(workflow, self.worker_id)
        result = await self.adapter.wait(
            prompt_id,
            timeout_seconds=int(payload.get("timeout_seconds", 900)),
        )
        return {"prompt_id": prompt_id, "history": result}
