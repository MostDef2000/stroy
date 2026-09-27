"""IP-Adapter identity conditioning tests (issue #63 step 3, manifest v0.3.0).

Covers: ReplacementRequest.ipa_weight acceptance, ipa_weight flowing into the
queued edit job payload (asset_roles["control_image"], idempotency suffix),
v0.3.0 manifest materialization with the IPAdapterFlux nodes, and the
executor's control_image handling (same cropped reference bytes, v0.2
payload compatibility, adapter provenance).
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from pydantic import ValidationError

from stroy.api.routes import ReplacementRequest
from stroy.generation import WorkflowManifest
from stroy.services import generations as gens
from stroy.worker.executors import ComfyUIExecutor

REPO_MANIFEST = Path("workflows/image-edit-kontext-v0.manifest.json")


def _edit_manifest() -> WorkflowManifest:
    return WorkflowManifest.model_validate_json(REPO_MANIFEST.read_text())


def _png_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# ReplacementRequest.ipa_weight
# ---------------------------------------------------------------------------


def _request_kwargs() -> dict:
    return dict(
        base_revision_id="rev-1",
        target_entity_id="sofa-1",
        reference_asset_id="asset-9",
        camera_id="default",
    )


def test_replacement_request_ipa_weight_default_and_bounds():
    request = ReplacementRequest(**_request_kwargs())
    assert request.ipa_weight == 0.85

    ok = ReplacementRequest(**_request_kwargs(), ipa_weight=0.0)
    assert ok.ipa_weight == 0.0
    ok = ReplacementRequest(**_request_kwargs(), ipa_weight=2.0)
    assert ok.ipa_weight == 2.0

    for bad in (-0.1, 2.1, 5.0):
        with pytest.raises(ValidationError):
            ReplacementRequest(**_request_kwargs(), ipa_weight=bad)


# ---------------------------------------------------------------------------
# queue_reference_edit payload flow
# ---------------------------------------------------------------------------


async def _queue_edit(monkeypatch, ipa_weight: float) -> dict:
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
        ipa_weight=ipa_weight,
    )
    return captured


@pytest.mark.asyncio
async def test_ipa_weight_flows_into_payload(monkeypatch):
    captured = await _queue_edit(monkeypatch, 1.25)
    payload = captured["payload"]

    # the SAME reference asset is exposed as the control_image role
    assert payload["asset_roles"]["reference_image"] == "asset-9"
    assert payload["asset_roles"]["control_image"] == "asset-9"
    # the control asset id is downloadable: it is the reference id, which is
    # listed in input_asset_ids (worker download_urls are keyed by asset id)
    assert "asset-9" in payload["input_asset_ids"]
    assert payload["ipa_weight"] == 1.25
    assert ":ipa1.25" in captured["idempotency_key"]


@pytest.mark.asyncio
async def test_ipa_weight_distinguishes_idempotency_keys(monkeypatch):
    key_default = (await _queue_edit(monkeypatch, 0.85))["idempotency_key"]
    key_zero = (await _queue_edit(monkeypatch, 0.0))["idempotency_key"]
    key_full = (await _queue_edit(monkeypatch, 1.0))["idempotency_key"]
    assert len({key_default, key_zero, key_full}) == 3
    assert key_default.endswith(":ipa0.85")
    assert key_zero.endswith(":ipa0.0")
    assert key_full.endswith(":ipa1.0")


@pytest.mark.asyncio
async def test_ipa_weight_zero_produces_valid_payload(monkeypatch):
    """ipa_weight=0.0 keeps the full v0.2.0-shaped payload (all roles bound);
    the IP-Adapter nodes simply contribute zero weight at sampling time."""
    captured = await _queue_edit(monkeypatch, 0.0)
    payload = captured["payload"]

    assert payload["ipa_weight"] == 0.0
    assert payload["asset_roles"] == {
        "base_image": "base-1",
        "reference_image": "asset-9",
        "mask_image": "mask-1",
        "control_image": "asset-9",
    }
    manifest = WorkflowManifest.model_validate(payload["workflow_manifest"])
    graph = manifest.materialize(
        {
            "prompt": payload["inputs"]["prompt"],
            "seed": payload["inputs"]["seed"],
            "base_image": "base.png",
            "reference_image": "ref.png",
            "mask_image": "mask.png",
            "control_image": "control.png",
        }
    )
    assert graph["13"]["inputs"]["model"] == ["27", 0]
    assert graph["27"]["inputs"]["weight"] == 0.85


# ---------------------------------------------------------------------------
# v0.3.0 manifest materialization
# ---------------------------------------------------------------------------


def test_manifest_v030_materializes_with_control_image():
    manifest = _edit_manifest()
    assert manifest.version == "0.3.0"
    assert manifest.required_inputs == [
        "prompt",
        "seed",
        "base_image",
        "reference_image",
        "mask_image",
        "control_image",
    ]
    assert manifest.bindings["control_image"].node_id == "25"
    assert manifest.bindings["control_image"].input_name == "image"

    graph = manifest.materialize(
        {
            "prompt": "a blue chair",
            "seed": 1,
            "base_image": "base.png",
            "reference_image": "ref.png",
            "mask_image": "mask.png",
            "control_image": "control.png",
        }
    )
    # VERIFY-PHASE1b wiring (input names are best-guess until box object_info)
    assert graph["23"]["class_type"] == "CLIPVisionLoader"
    assert graph["24"]["class_type"] == "IPAdapterModelLoader"
    assert graph["25"]["class_type"] == "LoadImage"
    assert graph["27"]["class_type"] == "IPAdapterFlux"
    assert graph["27"]["inputs"]["model"] == ["3", 0]
    assert graph["27"]["inputs"]["ipadapter"] == ["24", 0]
    assert graph["27"]["inputs"]["clip_vision"] == ["23", 0]
    assert graph["27"]["inputs"]["image"] == ["25", 0]
    assert graph["27"]["inputs"]["weight"] == 0.85
    # KSampler consumes the IP-Adapter-patched model
    assert graph["13"]["inputs"]["model"] == ["27", 0]
    # ReferenceLatent retained as locality hint: 9 -> 11 -> 12 -> 21
    assert graph["12"]["class_type"] == "ReferenceLatent"
    assert graph["12"]["inputs"]["conditioning"] == ["11", 0]
    assert graph["21"]["inputs"]["conditioning"] == ["12", 0]


def test_manifest_v030_rejects_missing_control_image():
    manifest = _edit_manifest()
    with pytest.raises(ValueError, match="control_image"):
        manifest.materialize(
            {
                "prompt": "p",
                "seed": 1,
                "base_image": "base.png",
                "reference_image": "ref.png",
                "mask_image": "mask.png",
            }
        )


# ---------------------------------------------------------------------------
# Executor: control_image upload + provenance
# ---------------------------------------------------------------------------


class StubClient:
    """Serves the replacement asset URLs; serves a real PNG for the
    reference asset so cropping runs. Counts downloads per asset id."""

    URLS = {
        "asset-base": "/inputs/asset-base",
        "asset-ref": "/inputs/asset-ref",
        "asset-mask": "/inputs/asset-mask",
    }

    def __init__(self) -> None:
        self.downloads: list[str] = []

    async def download_input(self, url: str) -> bytes:
        self.downloads.append(url)
        if url == self.URLS["asset-ref"]:
            return _png_bytes(100, 100, (10, 20, 30))
        for known in self.URLS.values():
            if url == known:
                return b"bytes"
        raise ValueError(f"unexpected download url: {url}")


class RecordingAdapter:
    """Adapter double that records uploads and the submitted graph."""

    def __init__(self) -> None:
        self.upload_calls: list[tuple[str, bytes]] = []
        self.submitted_graph: dict | None = None

    async def free_memory(self) -> bool:
        return True

    async def upload_image(self, filename: str, data: bytes) -> str:
        self.upload_calls.append((filename, data))
        return filename

    async def submit(self, graph: dict, worker_id: str) -> str:
        self.submitted_graph = graph
        return "prompt-1"

    async def wait(self, prompt_id: str, timeout_seconds: int) -> dict:
        return {"outputs": {}}

    async def collect_output_images(self, history: dict) -> list:
        return []

    async def server_version(self) -> str | None:
        return "0.3.5"


def _v03_job(asset_roles: dict | None = None) -> dict:
    return {
        "job_id": "job-1",
        "download_urls": dict(StubClient.URLS),
        "payload": {
            "workflow_manifest": _edit_manifest().model_dump(mode="json"),
            "inputs": {"prompt": "replace the chair", "seed": 7},
            "ipa_weight": 1.25,
            "asset_roles": asset_roles
            if asset_roles is not None
            else {
                "base_image": "asset-base",
                "reference_image": "asset-ref",
                "mask_image": "asset-mask",
                "control_image": "asset-ref",
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
async def test_executor_uploads_control_image_with_reference_bytes():
    client = StubClient()
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=client)  # type: ignore[arg-type]

    result = await executor.execute(_v03_job())

    # the reference asset is downloaded ONCE and uploaded under both roles
    assert client.downloads.count(StubClient.URLS["asset-ref"]) == 1
    uploads = dict(adapter.upload_calls)
    assert uploads["reference_image_asset-ref.png"] == (
        uploads["control_image_asset-ref.png"]
    )
    graph = adapter.submitted_graph
    assert graph is not None
    assert graph["25"]["inputs"]["image"] == "control_image_asset-ref.png"
    assert graph["7"]["inputs"]["image"] == "reference_image_asset-ref.png"
    assert graph["13"]["inputs"]["model"] == ["27", 0]
    # provenance records the IP-Adapter identity path (VERIFY-PHASE1b names)
    assert result["adapter_provenance"]["ipadapter_model"] == (
        "ip-adapter-flux.safetensors"
    )
    assert result["adapter_provenance"]["ipadapter_weight"] == 1.25
    assert result["adapter_provenance"]["ipadapter_clip"] == (
        "CLIP-ViT-H-14-laion2b-s32b-b4k.safetensors"
    )


@pytest.mark.asyncio
async def test_executor_crops_control_image_with_reference():
    client = StubClient()
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=client)  # type: ignore[arg-type]
    job = _v03_job()
    job["payload"]["reference_subject_bbox"] = [10, 10, 20, 20]

    await executor.execute(job)

    uploads = dict(adapter.upload_calls)
    ref_bytes = uploads["reference_image_asset-ref.png"]
    control_bytes = uploads["control_image_asset-ref.png"]
    # both roles carry the SAME cropped PNG bytes (no second crop/upload path)
    assert ref_bytes == control_bytes
    out = Image.open(io.BytesIO(control_bytes)).convert("RGB")
    assert out.size == (20, 20)
    assert out.getpixel((0, 0)) == (10, 20, 30)


@pytest.mark.asyncio
async def test_executor_derives_control_image_from_v02_payload():
    """v0.2.0 payloads expose only reference_image; with the v0.3.0 manifest
    the executor derives the control_image role from the same asset."""
    client = StubClient()
    adapter = RecordingAdapter()
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family", client=client)  # type: ignore[arg-type]

    await executor.execute(
        _v03_job(
            asset_roles={
                "base_image": "asset-base",
                "reference_image": "asset-ref",
                "mask_image": "asset-mask",
            }
        )
    )

    uploads = dict(adapter.upload_calls)
    assert uploads["control_image_asset-ref.png"] == (
        uploads["reference_image_asset-ref.png"]
    )
    graph = adapter.submitted_graph
    assert graph is not None
    assert graph["25"]["inputs"]["image"] == "control_image_asset-ref.png"
