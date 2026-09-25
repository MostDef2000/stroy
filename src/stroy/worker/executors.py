from __future__ import annotations

from typing import Any

from stroy.services.adapters import ComfyUIAdapter, OpenAICompatibleLLM


class QwenExecutor:
    def __init__(self, adapter: OpenAICompatibleLLM) -> None:
        self.adapter = adapter

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload", {})
        return await self.adapter.complete(payload.get("messages", []), tools=payload.get("tools"))


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
