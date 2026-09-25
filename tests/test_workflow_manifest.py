import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from stroy.generation import WorkflowManifest
from stroy.services.adapters import ComfyUIAdapter
from stroy.worker.executors import ComfyUIExecutor


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "workflow-manifest.json"


def manifest_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_workflow_manifest_matches_json_schema() -> None:
    payload = manifest_payload()
    schema = json.loads(
        (ROOT / "schemas" / "workflow-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(payload, schema)
    manifest = WorkflowManifest.model_validate(payload)
    assert manifest.id == "test-semantic-workflow"


def test_workflow_materializes_semantic_inputs_without_exposing_nodes_to_callers() -> None:
    manifest = WorkflowManifest.model_validate(manifest_payload())
    graph = manifest.materialize(
        {
            "positive_prompt": "warm minimal living room",
            "seed": 42,
        }
    )
    assert graph["6"]["inputs"]["text"] == "warm minimal living room"
    assert graph["25"]["inputs"]["noise_seed"] == 42
    assert manifest.graph["6"]["inputs"]["text"] == ""


def test_workflow_rejects_missing_required_semantic_input() -> None:
    manifest = WorkflowManifest.model_validate(manifest_payload())
    with pytest.raises(ValueError, match="missing required workflow inputs"):
        manifest.materialize({"positive_prompt": "hello"})


def test_workflow_rejects_missing_bound_node() -> None:
    payload = manifest_payload()
    payload["bindings"]["seed"]["node_id"] = "missing"
    manifest = WorkflowManifest.model_validate(payload)
    with pytest.raises(ValueError, match="missing node"):
        manifest.materialize({"positive_prompt": "hello", "seed": 1})


@pytest.mark.asyncio
async def test_comfy_executor_materializes_manifest_and_records_provenance() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            assert body["prompt"]["6"]["inputs"]["text"] == "beige sofa"
            assert body["prompt"]["25"]["inputs"]["noise_seed"] == 99
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "outputs": {
                            "42": {
                                "images": [
                                    {
                                        "filename": "output.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ComfyUIAdapter("http://comfy", client=client)
    executor = ComfyUIExecutor(adapter, "worker-1", "flux1-schnell")
    result = await executor.execute(
        {
            "payload": {
                "workflow_manifest": manifest_payload(),
                "inputs": {
                    "positive_prompt": "beige sofa",
                    "seed": 99,
                },
            }
        }
    )
    assert result["workflow"] == {
        "id": "test-semantic-workflow",
        "version": "0.1.0",
    }
    assert result["model_profile"] == "flux1-schnell"
    assert result["semantic_outputs"] == ["image"]
    assert result["adapter_provenance"] == {"adapter": "comfyui"}
    await client.aclose()


@pytest.mark.asyncio
async def test_comfy_executor_rejects_profile_mismatch() -> None:
    adapter = ComfyUIAdapter(
        "http://comfy",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500)
            )
        ),
    )
    executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family")
    with pytest.raises(ValueError, match="does not match"):
        await executor.execute(
            {
                "payload": {
                    "workflow_manifest": manifest_payload(),
                    "inputs": {
                        "positive_prompt": "test",
                        "seed": 1,
                    },
                }
            }
        )
    await adapter.client.aclose()
