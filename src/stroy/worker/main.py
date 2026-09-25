from __future__ import annotations

import asyncio
import os

from stroy.models import ModelProfileRegistry
from stroy.services.adapters import ComfyUIAdapter, FakeLLMAdapter, OpenAICompatibleLLM
from stroy.worker.client import WorkerClient
from stroy.worker.executors import ComfyUIExecutor, QwenExecutor
from stroy.worker.runtime import FakeExecutor, WorkerRunner


async def _run() -> None:
    server = os.getenv("STROY_SERVER_URL", "http://127.0.0.1:8000")
    worker_id = os.getenv("STROY_WORKER_ID", "local-fake-worker")
    token = os.getenv("STROY_WORKER_TOKEN", "development-worker-token")
    poll = float(os.getenv("STROY_WORKER_POLL_SECONDS", "5"))
    heartbeat = float(os.getenv("STROY_WORKER_HEARTBEAT_SECONDS", "20"))
    mode = os.getenv("STROY_WORKER_EXECUTOR_MODE", "fake")

    client = WorkerClient(server, token, worker_id)
    if mode == "local":
        registry = ModelProfileRegistry.load(
            os.getenv("STROY_MODEL_PROFILES_PATH", "config/model-profiles.json")
        )
        approved_use = os.getenv("STROY_MODEL_USE", "personal-non-commercial")
        llm_profile = registry.get(
            os.getenv("STROY_LLM_MODEL_PROFILE", "qwen3-14b"),
            kind="llm",
            approved_use=approved_use,
        )
        image_profile = registry.get(
            os.getenv("STROY_IMAGE_MODEL_PROFILE", "flux1-schnell"),
            kind="image",
            approved_use=approved_use,
        )
        llm = OpenAICompatibleLLM(
            os.getenv("STROY_LLM_BASE_URL", "http://127.0.0.1:8001/v1"),
            os.getenv("STROY_LLM_API_KEY", "local"),
            llm_profile.upstream,
            profile_id=llm_profile.id,
        )
        comfy = ComfyUIAdapter(os.getenv("STROY_COMFYUI_URL", "http://127.0.0.1:8188"))
        executors = {
            "llm.complete": QwenExecutor(llm),
            "style.analyze": QwenExecutor(llm),
            "image.generate": ComfyUIExecutor(comfy, worker_id, image_profile.id),
            "image.edit": ComfyUIExecutor(comfy, worker_id, image_profile.id),
            "render.blender": FakeExecutor("pending-blender-adapter"),
            "quality.geometry_check": FakeExecutor("pending-quality-adapter"),
        }
        models = [llm_profile.id, image_profile.id]
    else:
        executors = {
            "llm.complete": QwenExecutor(FakeLLMAdapter()),
            "style.analyze": FakeExecutor("fake-style"),
            "render.blender": FakeExecutor("fake-blender"),
            "image.generate": FakeExecutor("fake-image"),
            "image.edit": FakeExecutor("fake-image-edit"),
            "quality.geometry_check": FakeExecutor("fake-quality"),
        }
        models = ["fake"]

    await client.register(
        {
            "schema_version": "0.1.0",
            "worker_id": worker_id,
            "display_name": os.getenv("STROY_WORKER_NAME", "Home GPU worker"),
            "capabilities": [
                "llm",
                "style_analysis",
                "image_generation",
                "image_edit",
                "blender_render",
                "geometry_quality",
            ],
            "models": models,
            "runtimes": {mode: {"status": "ready", "version": "0.1.0"}},
            "hardware": {},
        }
    )
    await WorkerRunner(
        client,
        executors,
        poll_seconds=poll,
        heartbeat_seconds=heartbeat,
        lease_renew_seconds=heartbeat,
    ).run_forever()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
