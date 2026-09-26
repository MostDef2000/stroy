"""Real image_edit via flux-kontext: adapter upload, executor reference
resolution, server-side manifest routing, and manifest materialization."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from stroy.generation import WorkflowManifest
from stroy.services.adapters import (
    AdapterProtocolError,
    AdapterUnavailable,
    ComfyUIAdapter,
)
from stroy.services import generations as gens
from stroy.services.generations import ensure_generation_payload
from stroy.worker.executors import ComfyUIExecutor

REPO_MANIFEST = Path("workflows/image-edit-kontext-v0.manifest.json")


def _edit_manifest() -> WorkflowManifest:
    return WorkflowManifest.model_validate_json(REPO_MANIFEST.read_text())


def _upload_adapter(handler) -> tuple[ComfyUIAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ComfyUIAdapter("http://comfy", client=client), client


@pytest.mark.asyncio
async def test_adapter_upload_image_success():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/upload/image":
            return httpx.Response(200, json={"name": "uploaded_image.png"})
        return httpx.Response(404)

    adapter, client = _upload_adapter(handler)
    try:
        assert await adapter.upload_image("test.png", b"bytes") == "uploaded_image.png"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_adapter_upload_image_4xx():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content="Bad Request")

    adapter, client = _upload_adapter(handler)
    try:
        with pytest.raises(AdapterProtocolError):
            await adapter.upload_image("test.png", b"bytes")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_adapter_upload_image_5xx():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content="Server Error")

    adapter, client = _upload_adapter(handler)
    try:
        with pytest.raises(AdapterUnavailable):
            await adapter.upload_image("test.png", b"bytes")
    finally:
        await client.aclose()


class StubClient:
    """Serves exactly one asset URL; raises on anything else."""

    async def download_input(self, url: str) -> bytes:
        if url == "/api/v1/workers/jobs/job-1/inputs/asset-1":
            return b"fake-image-bytes"
        raise ValueError(f"unexpected download url: {url}")


class RecordingAdapter:
    """Minimal adapter double that records the submitted graph."""

    def __init__(self, uploaded_name: str = "uploaded_ref.png") -> None:
        self.uploaded_name = uploaded_name
        self.upload_calls: list[tuple[str, bytes]] = []
        self.submitted_graph: dict[str, Any] | None = None

    async def free_memory(self) -> bool:
        return True

    async def upload_image(self, filename: str, data: bytes) -> str:
        self.upload_calls.append((filename, data))
        return self.uploaded_name

    async def submit(self, graph: dict[str, Any], worker_id: str) -> str:
        self.submitted_graph = graph
        return "prompt-1"

    async def wait(self, prompt_id: str, timeout_seconds: int) -> dict[str, Any]:
        return {"outputs": {}}

    async def collect_output_images(self, history: dict[str, Any]) -> list:
        return []

    async def server_version(self) -> str | None:
        return "0.3.5"


def _reference_job() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "download_urls": {
            "asset-1": "/api/v1/workers/jobs/job-1/inputs/asset-1"
        },
        "payload": {
            "workflow_manifest": _edit_manifest().model_dump(mode="json"),
            "inputs": {"prompt": "replace the chair with a wooden one", "seed": 7},
            "generation": {
                "generation_id": "gen-1",
                "scene_revision_id": "rev-1",
                "design_revision_id": "rev-1",
                "camera_id": "default",
                "input_asset_ids": ["asset-1"],
            },
        },
    }


@pytest.mark.asyncio
async def test_executor_reference_image_flow():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=StubClient())

    await executor.execute(_reference_job())

    # reference downloaded and uploaded to ComfyUI under a deterministic name
    assert adapter.upload_calls == [("ref_asset-1.png", b"fake-image-bytes")]
    # the submitted graph binds the uploaded name, prompt and seed into the
    # REAL kontext manifest nodes
    graph = adapter.submitted_graph
    assert graph is not None
    assert graph["6"]["inputs"]["image"] == "uploaded_ref.png"
    assert graph["9"]["inputs"]["text"] == "replace the chair with a wooden one"
    assert graph["13"]["inputs"]["seed"] == 7


@pytest.mark.asyncio
async def test_executor_missing_reference_raises_error():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=StubClient())

    job = _reference_job()
    job["download_urls"] = {}
    job["payload"]["generation"]["input_asset_ids"] = []

    with pytest.raises(ValueError, match="input_asset_ids"):
        await executor.execute(job)
    assert adapter.upload_calls == []


@pytest.mark.asyncio
async def test_executor_requires_client_for_reference():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=None)

    with pytest.raises(ValueError, match="requires a client"):
        await executor.execute(_reference_job())


@pytest.mark.asyncio
async def test_ensure_generation_payload_routes_edit_manifest():
    edit = ensure_generation_payload({"prompt": "x"}, job_type="image.edit")
    assert edit["workflow_manifest"]["id"] == "image-edit-kontext-v0"
    assert edit["workflow_manifest"]["model_profile"] == "flux-dev-family"

    generate = ensure_generation_payload({"prompt": "x"}, job_type="image.generate")
    assert generate["workflow_manifest"]["id"] == "flux-redesign-v0"


@pytest.mark.asyncio
async def test_queue_reference_edit_embeds_edit_manifest(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_create_job(session, **kwargs):
        captured.update(kwargs)
        return MagicMock(job_type=kwargs["job_type"])

    monkeypatch.setattr(gens, "create_job", fake_create_job)

    await gens.queue_reference_edit(
        MagicMock(),
        project_id="p1",
        design_revision_id="rev-2",
        camera_id="default",
        request_text="swap the sofa",
        target_entity_id="sofa-1",
        reference_asset_id="asset-9",
        affected_region={"x": 0, "y": 0},
        protected_entity_ids=["wall-1"],
        correlation_id=None,
        dispatcher=None,
    )

    assert captured["job_type"] == "image.edit"
    assert captured["required_capabilities"] == ["image_edit"]
    manifest = captured["payload"]["workflow_manifest"]
    assert manifest["id"] == "image-edit-kontext-v0"
    assert manifest["required_inputs"] == ["prompt", "seed", "reference_image"]
    assert captured["payload"]["generation"]["input_asset_ids"] == ["asset-9"]


def test_manifest_materialization():
    manifest = _edit_manifest()
    assert manifest.id == "image-edit-kontext-v0"
    assert manifest.schema_version == "0.1.0"
    assert manifest.model_profile == "flux-dev-family"

    graph = manifest.materialize(
        {
            "prompt": "a blue chair",
            "seed": 12345,
            "reference_image": "ref_asset_123.png",
        }
    )
    assert graph["9"]["inputs"]["text"] == "a blue chair"
    assert graph["13"]["inputs"]["seed"] == 12345
    assert graph["6"]["inputs"]["image"] == "ref_asset_123.png"
    # kontext wiring sanity: latent and reference latent both come from the
    # encoded scaled reference, negative stays a plain empty conditioning
    assert graph["12"]["inputs"]["latent"] == ["8", 0]
    assert graph["13"]["inputs"]["latent_image"] == ["8", 0]
    assert graph["13"]["inputs"]["negative"] == ["10", 0]
