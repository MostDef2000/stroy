import asyncio

import pytest

from stroy.worker.runtime import WorkerRunner


class FakeClient:
    def __init__(self) -> None:
        self.heartbeats = 0
        self.renews = 0
        self.started = 0
        self.completed = 0
        self.failed = 0
        self.progress_updates = 0
        self.worker_id = "worker-test"
        self.uploaded = 0
        self.last_result = None
        self.last_runtime_provenance = None
        self.uploaded_semantics = {}

    async def heartbeat(self) -> None:
        self.heartbeats += 1

    async def claim(self):
        return {
            "job_id": "job-1",
            "lease_id": "lease-1",
            "job_type": "slow",
            "payload": {},
        }

    async def start(self, job_id: str, lease_id: str) -> None:
        self.started += 1

    async def renew(self, job_id: str, lease_id: str) -> None:
        self.renews += 1

    async def progress(
        self,
        job_id: str,
        lease_id: str,
        progress: dict,
        runtime_provenance: dict | None = None,
    ) -> None:
        self.progress_updates += 1

    async def upload_output(
        self,
        job_id: str,
        lease_id: str,
        *,
        filename: str,
        data: bytes,
        media_type: str = "application/octet-stream",
        semantic_name: str | None = None,
    ) -> dict:
        self.uploaded += 1
        assert filename
        assert data
        asset_id = f"asset-output-{self.uploaded}"
        if semantic_name:
            self.uploaded_semantics[semantic_name] = asset_id
        return {"id": asset_id}

    async def complete(
        self,
        job_id: str,
        lease_id: str,
        result: dict,
        runtime_provenance: dict | None = None,
    ) -> None:
        self.completed += 1
        self.last_result = result
        self.last_runtime_provenance = runtime_provenance

    async def fail(
        self,
        job_id: str,
        lease_id: str,
        error: dict,
        runtime_provenance: dict | None = None,
    ) -> None:
        self.failed += 1


class SlowExecutor:
    async def execute(self, job: dict) -> dict:
        await asyncio.sleep(0.04)
        return {"ok": True}


@pytest.mark.asyncio
async def test_worker_renews_lease_while_executor_runs() -> None:
    client = FakeClient()
    runner = WorkerRunner(
        client,
        {"slow": SlowExecutor()},
        heartbeat_seconds=0.001,
        lease_renew_seconds=0.005,
    )

    assert await runner.run_once() is True
    assert client.heartbeats == 1
    assert client.started == 1
    assert client.progress_updates == 1
    assert client.renews >= 1
    assert client.completed == 1
    assert client.failed == 0



class GenerationExecutor:
    async def execute(self, job: dict) -> dict:
        return {
            "workflow": {"id": "workflow-1", "version": "0.1.0"},
            "model_profile": "flux1-schnell",
            "adapter_provenance": {"adapter": "comfyui"},
            "_artifacts": [
                {
                    "filename": "render.png",
                    "media_type": "image/png",
                    "data": b"generated-image",
                }
            ],
            "_generation_context": {
                "generation_id": "generation-1",
                "scene_revision_id": "scene-rev-1",
                "design_revision_id": "design-rev-1",
                "camera_id": "camera.living.entry",
                "seed": 123,
                "input_asset_ids": ["asset-input-1"],
                "structured_conditioning": {"prompt": "warm minimal"},
            },
        }


@pytest.mark.asyncio
async def test_worker_uploads_generated_artifacts_before_completion() -> None:
    client = FakeClient()
    client.claim = lambda: _claim_generation_job()
    runner = WorkerRunner(
        client,
        {"image.generate": GenerationExecutor()},
        heartbeat_seconds=60,
        lease_renew_seconds=60,
    )

    assert await runner.run_once() is True
    assert client.uploaded == 1
    assert client.completed == 1
    manifest = client.last_result["generation_manifest"]
    assert manifest["generation_id"] == "generation-1"
    assert manifest["workflow"] == {"id": "workflow-1", "version": "0.1.0"}
    assert manifest["model_profile"] == "flux1-schnell"
    assert manifest["input_asset_ids"] == ["asset-input-1"]
    assert manifest["output_asset_ids"] == ["asset-output-1"]
    assert client.last_result["output_asset_ids"] == ["asset-output-1"]
    assert client.last_runtime_provenance == {
        "worker_id": "worker-test",
        "adapter": "comfyui",
    }


async def _claim_generation_job():
    return {
        "job_id": "job-generation",
        "lease_id": "lease-generation",
        "job_type": "image.generate",
        "payload": {},
    }



class RenderExecutor:
    async def execute(self, job: dict) -> dict:
        pass_names = ["rgb", "depth", "normals", "object_ids", "material_ids"]
        artifacts = [
            {
                "semantic_name": name,
                "filename": f"{name}.png" if name == "rgb" else f"{name}.exr",
                "media_type": "image/png" if name == "rgb" else "image/x-exr",
                "data": f"{name}-bytes".encode(),
            }
            for name in pass_names
        ]
        artifacts.append(
            {
                "semantic_name": "metadata",
                "filename": "scene_metadata.json",
                "media_type": "application/json",
                "data": b"{}",
            }
        )
        return {
            "_artifacts": artifacts,
            "_render_context": {
                "render_id": "render-1",
                "scene_revision_id": "scene-rev-1",
                "camera_id": "camera.main",
                "renderer_profile": "blender-cycles-v0",
            },
        }


@pytest.mark.asyncio
async def test_worker_finalizes_render_manifest_from_semantic_outputs() -> None:
    client = FakeClient()
    client.claim = lambda: _claim_render_job()
    runner = WorkerRunner(
        client,
        {"render.blender": RenderExecutor()},
        heartbeat_seconds=60,
        lease_renew_seconds=60,
    )

    assert await runner.run_once() is True
    assert client.completed == 1
    assert client.failed == 0
    assert client.uploaded == 6

    manifest = client.last_result["render_manifest"]
    assert manifest["render_id"] == "render-1"
    assert manifest["scene_revision_id"] == "scene-rev-1"
    assert manifest["camera_id"] == "camera.main"
    assert manifest["passes"] == {
        name: client.uploaded_semantics[name]
        for name in sorted(
            ["rgb", "depth", "normals", "object_ids", "material_ids"]
        )
    }


async def _claim_render_job():
    return {
        "job_id": "job-render",
        "lease_id": "lease-render",
        "job_type": "render.blender",
        "payload": {},
    }
