from __future__ import annotations

import json
from typing import Any, Protocol

from stroy.generation import WorkflowManifest
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
    def __init__(
        self,
        adapter: ComfyUIAdapter,
        worker_id: str,
        model_profile_id: str,
    ) -> None:
        self.adapter = adapter
        self.worker_id = worker_id
        self.model_profile_id = model_profile_id

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        raw_manifest = payload.get("workflow_manifest")
        if not isinstance(raw_manifest, dict):
            raise ValueError("image job requires payload.workflow_manifest")
        manifest = WorkflowManifest.model_validate(raw_manifest)
        if manifest.model_profile != self.model_profile_id:
            raise ValueError(
                "workflow model profile does not match worker image profile: "
                f"{manifest.model_profile} != {self.model_profile_id}"
            )

        semantic_inputs = payload.get("inputs") or {}
        if not isinstance(semantic_inputs, dict):
            raise ValueError("image job payload.inputs must be an object")
        graph = manifest.materialize(semantic_inputs)

        prompt_id = await self.adapter.submit(graph, self.worker_id)
        history = await self.adapter.wait(
            prompt_id,
            timeout_seconds=int(payload.get("timeout_seconds", 900)),
        )
        return {
            "prompt_id": prompt_id,
            "history": history,
            "workflow": {
                "id": manifest.id,
                "version": manifest.version,
            },
            "model_profile": manifest.model_profile,
            "semantic_outputs": manifest.outputs,
            "adapter_provenance": self.adapter.provenance(),
        }
