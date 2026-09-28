"""Real image_edit via flux-kontext: adapter upload, executor asset-input
resolution (v0.2 asset_roles + legacy fallback), server-side manifest
routing, and manifest materialization."""

from pathlib import Path

import httpx
import io
import pytest
from unittest.mock import MagicMock
from PIL import Image

from stroy.editing.mask import crop_image_bytes
from stroy.api.routes import ReplacementRequest
from stroy.generation import WorkflowManifest
from pydantic import ValidationError
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
    """Serves the three replacement asset URLs; raises on anything else."""

    URLS = {
        "asset-base": "/api/v1/workers/jobs/job-1/inputs/asset-base",
        "asset-ref": "/api/v1/workers/jobs/job-1/inputs/asset-ref",
        "asset-mask": "/api/v1/workers/jobs/job-1/inputs/asset-mask",
    }

    async def download_input(self, url: str) -> bytes:
        for asset_id, known in self.URLS.items():
            if url == known:
                return f"bytes-{asset_id}".encode()
        raise ValueError(f"unexpected download url: {url}")


class RecordingAdapter:
    """Minimal adapter double that records uploads and the submitted graph."""

    def __init__(self, uploaded_name: str = "uploaded.png") -> None:
        self.uploaded_name = uploaded_name
        self.upload_calls: list[tuple[str, bytes]] = []
        self.submitted_graph: dict | None = None

    async def free_memory(self) -> bool:
        return True

    async def upload_image(self, filename: str, data: bytes) -> str:
        self.upload_calls.append((filename, data))
        return f"{filename}"  # uploaded name echoes the requested filename

    async def submit(self, graph: dict, worker_id: str) -> str:
        self.submitted_graph = graph
        return "prompt-1"

    async def wait(self, prompt_id: str, timeout_seconds: int) -> dict:
        return {"outputs": {}}

    async def collect_output_images(self, history: dict) -> list:
        return []

    async def server_version(self) -> str | None:
        return "0.3.5"


def _v02_job() -> dict:
    return {
        "job_id": "job-1",
        "download_urls": {k: v for k, v in StubClient.URLS.items()},
        "payload": {
            "workflow_manifest": _edit_manifest().model_dump(mode="json"),
            "inputs": {"prompt": "replace the chair", "seed": 7},
            "asset_roles": {
                "base_image": "asset-base",
                "reference_image": "asset-ref",
                "mask_image": "asset-mask",
            },
            "generation": {
                "generation_id": "gen-1",
                "scene_revision_id": "rev-1",
                "design_revision_id": "rev-1",
                "camera_id": "default",
                "input_asset_ids": ["asset-base", "asset-ref", "asset-mask"],
            },
        },
    }


@pytest.mark.asyncio
async def test_executor_asset_roles_flow():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=StubClient())  # type: ignore[arg-type]

    await executor.execute(_v02_job())

    # all role inputs downloaded and uploaded under deterministic names; the
    # v0.3.0 IP-Adapter control image reuses the reference bytes (same asset)
    assert [name for name, _ in adapter.upload_calls] == [
        "base_image_asset-base.png",
        "reference_image_asset-ref.png",
        "control_image_asset-ref.png",
        "mask_image_asset-mask.png",
    ]
    graph = adapter.submitted_graph
    assert graph is not None
    # the REAL v0.3 manifest wiring: base/reference/mask/control LoadImage
    # nodes bound, prompt and seed bound, mask feeds inpaint latent + both
    # conditionings, ApplyIPAdapterFlux (27) patches the UNET into KSampler 13
    assert graph["6"]["inputs"]["image"] == "base_image_asset-base.png"
    assert graph["7"]["inputs"]["image"] == "reference_image_asset-ref.png"
    assert graph["17"]["inputs"]["image"] == "mask_image_asset-mask.png"
    assert graph["25"]["inputs"]["image"] == "control_image_asset-ref.png"
    assert graph["9"]["inputs"]["text"] == "replace the chair"
    assert graph["13"]["inputs"]["seed"] == 7
    assert graph["13"]["inputs"]["model"] == ["27", 0]
    assert graph["27"]["class_type"] == "ApplyIPAdapterFlux"
    assert graph["27"]["inputs"]["image"] == ["25", 0]
    assert graph["20"]["inputs"]["mask"] == ["18", 0]
    assert graph["13"]["inputs"]["latent_image"] == ["20", 0]
    assert graph["13"]["inputs"]["positive"] == ["21", 0]


def _legacy_job() -> dict:
    legacy_manifest = {
        "id": "image-edit-kontext-v0",
        "version": "0.1.0",
        "model_profile": "flux-dev-family",
        "required_inputs": ["prompt", "seed", "reference_image"],
        "outputs": ["image"],
        "schema_version": "0.1.0",
        "bindings": {
            "prompt": {"node_id": "9", "input_name": "text"},
            "seed": {"node_id": "13", "input_name": "seed"},
            "reference_image": {"node_id": "6", "input_name": "image"},
        },
        "graph": {
            "6": {"class_type": "LoadImage", "inputs": {"image": ""}},
            "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 0]}},
            "13": {"class_type": "KSampler", "inputs": {"seed": 0}},
        },
    }
    return {
        "job_id": "job-legacy",
        "download_urls": {"asset-ref": StubClient.URLS["asset-ref"]},
        "payload": {
            "workflow_manifest": legacy_manifest,
            "inputs": {"prompt": "p", "seed": 1},
            "generation": {
                "generation_id": "gen-2",
                "scene_revision_id": "rev-2",
                "design_revision_id": "rev-2",
                "camera_id": "default",
                "input_asset_ids": ["asset-ref"],
            },
        },
    }


@pytest.mark.asyncio
async def test_executor_legacy_reference_fallback():
    """v0.1.0 manifests without asset_roles keep the first-input-asset path."""
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=StubClient())  # type: ignore[arg-type]

    await executor.execute(_legacy_job())

    assert adapter.upload_calls == [("reference_image_asset-ref.png", b"bytes-asset-ref")]
    graph = adapter.submitted_graph
    assert graph is not None
    assert graph["6"]["inputs"]["image"] == "reference_image_asset-ref.png"


@pytest.mark.asyncio
async def test_executor_missing_asset_roles_raises():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=StubClient())  # type: ignore[arg-type]

    job = _v02_job()
    del job["payload"]["asset_roles"]

    with pytest.raises(ValueError, match="asset_roles"):
        await executor.execute(job)
    assert adapter.upload_calls == []


@pytest.mark.asyncio
async def test_executor_requires_client_for_assets():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="requires a client"):
        await executor.execute(_v02_job())


@pytest.mark.asyncio
async def test_ensure_generation_payload_routes_edit_manifest():
    edit = ensure_generation_payload({"prompt": "x"}, job_type="image.edit")
    assert edit["workflow_manifest"]["id"] == "image-edit-kontext-v0"
    assert edit["workflow_manifest"]["version"] == "0.3.1"
    assert edit["workflow_manifest"]["model_profile"] == "flux-dev-family"

    generate = ensure_generation_payload({"prompt": "x"}, job_type="image.generate")
    assert generate["workflow_manifest"]["id"] == "flux-redesign-v0"


@pytest.mark.asyncio
async def test_queue_reference_edit_embeds_edit_manifest(monkeypatch):
    captured: dict = {}

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
    assert manifest["required_inputs"] == [
        "prompt",
        "seed",
        "base_image",
        "reference_image",
        "mask_image",
        "control_image",
    ]
    assert captured["payload"]["generation"]["input_asset_ids"] == ["asset-9"]


def test_manifest_materialization():
    manifest = _edit_manifest()
    assert manifest.id == "image-edit-kontext-v0"
    assert manifest.version == "0.3.1"
    assert manifest.schema_version == "0.1.0"
    assert manifest.model_profile == "flux-dev-family"

    graph = manifest.materialize(
        {
            "prompt": "a blue chair",
            "seed": 12345,
            "base_image": "base.png",
            "reference_image": "ref.png",
            "mask_image": "mask.png",
            "control_image": "control.png",
        }
    )
    assert graph["6"]["inputs"]["image"] == "base.png"
    assert graph["7"]["inputs"]["image"] == "ref.png"
    assert graph["17"]["inputs"]["image"] == "mask.png"
    assert graph["25"]["inputs"]["image"] == "control.png"
    assert graph["9"]["inputs"]["text"] == "a blue chair"
    assert graph["13"]["inputs"]["seed"] == 12345
    # mask-constrained wiring sanity: node 12 (ReferenceLatent) conditions on
    # the base-scene latent (node 20, VAEEncodeForInpaint) so the locality hint
    # comes from the base frame, not the stretched reference crop; the inpaint
    # latent (with mask) drives sampling
    assert graph["12"]["inputs"]["latent"] == ["20", 0]
    assert graph["20"]["inputs"]["pixels"] == ["6", 0]
    assert graph["20"]["inputs"]["mask"] == ["18", 0]
    assert graph["21"]["inputs"]["mask"] == ["18", 0]
    assert graph["22"]["inputs"]["mask"] == ["18", 0]
    assert graph["13"]["inputs"]["latent_image"] == ["20", 0]
    assert graph["13"]["inputs"]["denoise"] == 1.0
    # v0.3.0 IP-Adapter identity path: node 27 patches the UNET, node 12
    # ReferenceLatent retained as locality hint
    assert graph["27"]["class_type"] == "ApplyIPAdapterFlux"
    assert graph["27"]["inputs"]["model"] == ["3", 0]
    assert graph["27"]["inputs"]["ipadapter_flux"] == ["24", 0]
    assert graph["27"]["inputs"]["image"] == ["25", 0]
    assert graph["27"]["inputs"]["weight"] == 0.85
    assert graph["13"]["inputs"]["model"] == ["27", 0]
    assert graph["12"]["inputs"]["conditioning"] == ["11", 0]
    assert graph["21"]["inputs"]["conditioning"] == ["12", 0]


def test_crop_image_bytes_returns_subregion():
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    px = img.load()
    for x in range(10, 30):
        for y in range(10, 30):
            px[x, y] = (255, 0, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    cropped = crop_image_bytes(buf.getvalue(), [10, 10, 20, 20])
    out = Image.open(io.BytesIO(cropped)).convert("RGB")
    assert out.size == (20, 20)
    assert out.getpixel((0, 0)) == (255, 0, 0)
    assert out.getpixel((19, 19)) == (255, 0, 0)


def test_crop_image_bytes_clamps_to_bounds():
    img = Image.new("RGB", (50, 50), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    cropped = crop_image_bytes(buf.getvalue(), [40, 40, 30, 30])
    out = Image.open(io.BytesIO(cropped)).convert("RGB")
    assert out.size == (10, 10)


def test_crop_image_bytes_rejects_bad_bbox():
    img = Image.new("RGB", (10, 10), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    for bad in ([1, 2, 3], [0, 0, 0, 5], [0, 0, -1, 5], [100, 100, 5, 5]):
        with pytest.raises(ValueError):
            crop_image_bytes(data, bad)


class CropStubClient:
    """Like StubClient but serves a real PNG for the reference so cropping runs."""

    URLS = StubClient.URLS

    async def download_input(self, url: str) -> bytes:
        if url == StubClient.URLS["asset-ref"]:
            buf = io.BytesIO()
            Image.new("RGB", (100, 100), (10, 20, 30)).save(buf, format="PNG")
            return buf.getvalue()
        for asset_id, known in self.URLS.items():
            if url == known:
                return f"bytes-{asset_id}".encode()
        raise ValueError(f"unexpected download url: {url}")


@pytest.mark.asyncio
async def test_executor_crops_reference_only():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=CropStubClient())  # type: ignore[arg-type]
    job = _v02_job()
    job["payload"]["reference_subject_bbox"] = [10, 10, 20, 20]
    await executor.execute(job)

    calls = {name: data for name, data in adapter.upload_calls}
    # base + mask are NOT cropped
    assert calls["base_image_asset-base.png"] == b"bytes-asset-base"
    assert calls["mask_image_asset-mask.png"] == b"bytes-asset-mask"
    # reference IS cropped to the bbox
    ref_bytes = calls["reference_image_asset-ref.png"]
    out = Image.open(io.BytesIO(ref_bytes)).convert("RGB")
    assert out.size == (20, 20)
    assert out.getpixel((0, 0)) == (10, 20, 30)


@pytest.mark.asyncio
async def test_executor_no_crop_without_bbox():
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=CropStubClient())  # type: ignore[arg-type]
    await executor.execute(_v02_job())
    calls = {name: data for name, data in adapter.upload_calls}
    out = Image.open(io.BytesIO(calls["reference_image_asset-ref.png"])).convert("RGB")
    assert out.size == (100, 100)


@pytest.mark.asyncio
async def test_queue_reference_edit_embeds_reference_crop(monkeypatch):
    captured: dict = {}

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
        base_asset_id="base-1",
        mask_asset_id="mask-1",
        reference_subject_bbox=[10, 20, 30, 40],
    )

    assert captured["payload"]["reference_subject_bbox"] == [10, 20, 30, 40]
    assert ":crop-" in captured["idempotency_key"]
    assert len(captured["idempotency_key"]) <= 160


def test_replacement_request_rejects_malformed_bbox():
    base = dict(
        base_revision_id="rev-1",
        target_entity_id="sofa-1",
        reference_asset_id="asset-9",
        camera_id="default",
    )
    # malformed bboxes are rejected with 422 at the API boundary
    for bad in ([1, 2, 3], [0, 0, 0, 5], [0, 0, -1, 5], []):
        with pytest.raises(ValidationError):
            ReplacementRequest(**base, reference_subject_bbox=bad)
    # valid bbox and absence are accepted
    ok = ReplacementRequest(**base, reference_subject_bbox=[10, 20, 30, 40])
    assert ok.reference_subject_bbox == [10, 20, 30, 40]
    none = ReplacementRequest(**base)
    assert none.reference_subject_bbox is None
