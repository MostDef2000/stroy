from __future__ import annotations

import asyncio
import os
import signal

from stroy.models import ModelProfileRegistry
from stroy.rendering import BlenderAdapter
from stroy.services.adapters import ComfyUIAdapter, FakeLLMAdapter, OpenAICompatibleLLM
from stroy.worker.client import WorkerClient
from stroy.worker.executors import (
    BlenderExecutor,
    ComfyUIExecutor,
    FakeImageExecutor,
    GeometryQualityExecutor,
    QwenExecutor,
    build_style_analyze_executor,
)
from stroy.worker.runtime import FakeExecutor, WorkerRunner


# Define capabilities in a way that allows environment override
DEFAULT_CAPABILITIES = [
    "llm",
    "style_analysis",
    "image_generation",
    "image_edit",
    "blender_render",
    "geometry_quality",
]

def parse_capabilities(raw_caps: str | None) -> list[str]:
    if not raw_caps:
        return DEFAULT_CAPABILITIES
    capabilities = [c.strip() for c in raw_caps.split(",") if c.strip()]
    unknown = set(capabilities) - set(DEFAULT_CAPABILITIES)
    if unknown:
        print(f"Error: unknown worker capability tokens: {unknown}")
        raise SystemExit(1)
    return capabilities

async def _run() -> None:
    server = os.getenv("STROY_SERVER_URL", "http://127.0.0.1:8000")
    worker_id = os.getenv("STROY_WORKER_ID", "local-fake-worker")
    token = os.getenv("STROY_WORKER_TOKEN", "development-worker-token")
    poll = float(os.getenv("STROY_WORKER_POLL_SECONDS", "5"))
    heartbeat = float(os.getenv("STROY_WORKER_HEARTBEAT_SECONDS", "20"))
    mode = os.getenv("STROY_WORKER_EXECUTOR_MODE", "fake")

    # Capability parsing
    capabilities = parse_capabilities(os.getenv("STROY_WORKER_CAPABILITIES"))

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
        blender = BlenderAdapter(
            os.getenv("STROY_BLENDER_BIN", "blender"),
            script_path=os.getenv("STROY_BLENDER_SCRIPT", "blender/stroy_blender.py"),
            timeout_seconds=int(os.getenv("STROY_BLENDER_TIMEOUT_SECONDS", "900")),
        )
        executors = {
            "llm.complete": QwenExecutor(llm),
            "style.analyze": QwenExecutor(llm),
            "image.generate": ComfyUIExecutor(comfy, worker_id, image_profile.id),
            "image.edit": ComfyUIExecutor(comfy, worker_id, image_profile.id),
            "render.blender": BlenderExecutor(blender),
            "quality.geometry_check": GeometryQualityExecutor(client),
        }
        models = [llm_profile.id, image_profile.id]
    else:
        executors = {
            "llm.complete": QwenExecutor(FakeLLMAdapter()),
            "style.analyze": build_style_analyze_executor(
                os.getenv("STROY_STYLE_VISION_ADAPTER", "mock"), client
            ),
            "render.blender": FakeExecutor("fake-blender"),
            "image.generate": FakeImageExecutor(),
            "image.edit": FakeImageExecutor(),
            "quality.geometry_check": FakeExecutor("fake-quality"),
        }
        models = ["fake"]

    await client.register(
        {
            "schema_version": "0.1.0",
            "worker_id": worker_id,
            "display_name": os.getenv("STROY_WORKER_NAME", "Home GPU worker"),
            "capabilities": capabilities,
            "models": models,
            "runtimes": {"comfyui" if mode == "local" else mode: {"status": "ready", "version": "0.1.0"}},
            "hardware": {},
        }
    )
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass

    runner = WorkerRunner(
        client,
        executors,
        poll_seconds=poll,
        heartbeat_seconds=heartbeat,
        lease_renew_seconds=heartbeat,
    )
    try:
        await runner.run_forever(shutdown)
    finally:
        await client.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
