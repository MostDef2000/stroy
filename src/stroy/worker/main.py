from __future__ import annotations

import asyncio
import os

from stroy.worker.client import WorkerClient
from stroy.worker.runtime import FakeExecutor, WorkerRunner


async def _run() -> None:
    server = os.getenv("STROY_SERVER_URL", "http://127.0.0.1:8000")
    worker_id = os.getenv("STROY_WORKER_ID", "local-fake-worker")
    token = os.getenv("STROY_WORKER_TOKEN", "development-worker-token")
    poll = float(os.getenv("STROY_WORKER_POLL_SECONDS", "5"))

    client = WorkerClient(server, token, worker_id)
    executors = {
        "llm.complete": FakeExecutor("fake-llm"),
        "style.analyze": FakeExecutor("fake-style"),
        "render.blender": FakeExecutor("fake-blender"),
        "image.generate": FakeExecutor("fake-image"),
        "image.edit": FakeExecutor("fake-image-edit"),
        "quality.geometry_check": FakeExecutor("fake-quality"),
    }
    await client.register(
        {
            "schema_version": "0.1.0",
            "worker_id": worker_id,
            "display_name": os.getenv("STROY_WORKER_NAME", "Local fake worker"),
            "capabilities": [
                "llm",
                "style_analysis",
                "image_generation",
                "image_edit",
                "blender_render",
                "geometry_quality",
            ],
            "models": ["fake"],
            "runtimes": {"fake": {"status": "ready", "version": "0.1.0"}},
            "hardware": {},
        }
    )
    await WorkerRunner(client, executors, poll_seconds=poll).run_forever()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
