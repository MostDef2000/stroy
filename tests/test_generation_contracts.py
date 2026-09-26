from pathlib import Path

import httpx
import pytest

from stroy.generation import GenerationContext, WorkflowManifest, finalize_generation_manifest
from stroy.services.adapters import ComfyUIAdapter
from stroy.worker.executors import ComfyUIExecutor


ROOT = Path(__file__).resolve().parents[1]


def manifest() -> WorkflowManifest:
    return WorkflowManifest.model_validate_json(
        (ROOT / "workflows" / "flux-redesign-v0.manifest.json").read_text(
            encoding="utf-8"
        )
    )


def test_workflow_materializes_semantic_inputs_without_mutating_manifest() -> None:
    workflow = manifest()
    graph = workflow.materialize({"prompt": "warm minimal", "seed": 123})

    assert graph["2"]["inputs"]["text"] == "warm minimal"
    assert graph["6"]["inputs"]["seed"] == 123
    assert workflow.graph["2"]["inputs"]["text"] == ""
    assert workflow.graph["6"]["inputs"]["seed"] == 0


def test_workflow_rejects_missing_required_input() -> None:
    with pytest.raises(ValueError, match="missing required workflow inputs"):
        manifest().materialize({"prompt": "warm minimal"})


def test_generation_manifest_records_exact_workflow_and_assets() -> None:
    context = GenerationContext(
        generation_id="generation-1",
        scene_revision_id="scene-rev-1",
        design_revision_id="design-rev-1",
        camera_id="camera.living.entry",
        seed=42,
        input_asset_ids=["asset-input"],
        structured_conditioning={"prompt": "warm minimal"},
    )
    finalized = finalize_generation_manifest(
        context=context,
        workflow_id="flux-redesign-v0",
        workflow_version="0.2.0",
        model_profile="flux-dev-family",
        output_asset_ids=["asset-output"],
    )

    assert finalized.workflow.id == "flux-redesign-v0"
    assert finalized.workflow.version == "0.2.0"
    assert finalized.model_profile == "flux-dev-family"
    assert finalized.input_asset_ids == ["asset-input"]
    assert finalized.output_asset_ids == ["asset-output"]


@pytest.mark.asyncio
async def test_comfy_executor_materializes_collects_and_reports_provenance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            payload = __import__("json").loads(request.content)
            assert payload["prompt"]["2"]["inputs"]["text"] == "warm minimal"
            assert payload["prompt"]["6"]["inputs"]["seed"] == 123
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "9": {
                                "images": [
                                    {
                                        "filename": "render.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            },
                        }
                    },
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=b"image-bytes")
        if request.url.path == "/free":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "1.0.0"}})
        raise AssertionError(f"unexpected request: {request.url}")


    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ComfyUIAdapter("http://comfy", client=client)
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family")

    result = await executor.execute(
        {
            "job_id": "job-1",
            "payload": {
                "workflow_manifest": manifest().model_dump(mode="json"),
                "inputs": {"prompt": "warm minimal", "seed": 123},
                "generation": {
                    "generation_id": "generation-1",
                    "scene_revision_id": "scene-rev-1",
                    "design_revision_id": "design-rev-1",
                    "camera_id": "camera.living.entry",
                    "seed": 123,
                    "input_asset_ids": [],
                    "structured_conditioning": {"prompt": "warm minimal"},
                },
            },
        }
    )

    assert result["workflow"] == {"id": "flux-redesign-v0", "version": "0.2.0"}
    assert result["model_profile"] == "flux-dev-family"
    assert result["semantic_outputs"] == ["image"]
    assert result["_artifacts"][0]["filename"] == "render.png"
    assert result["_artifacts"][0]["data"] == b"image-bytes"
    await client.aclose()


@pytest.mark.asyncio
async def test_comfy_executor_rejects_wrong_worker_model_profile() -> None:
    adapter = ComfyUIAdapter(
        "http://comfy",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={})
            )
        ),
    )
    executor = ComfyUIExecutor(adapter, "worker-1", "wrong-profile")
    with pytest.raises(ValueError, match="does not match worker image profile"):
        await executor.execute(
            {
                "job_id": "job-1",
                "payload": {
                    "workflow_manifest": manifest().model_dump(mode="json"),
                    "inputs": {"prompt": "warm minimal", "seed": 123},
                    "generation": {
                        "generation_id": "generation-1",
                        "scene_revision_id": "scene-rev-1",
                        "design_revision_id": "design-rev-1",
                        "camera_id": "camera.living.entry",
                    },
                },
            }
        )
    await adapter.client.aclose()
