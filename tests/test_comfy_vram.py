import pytest
import os
import httpx
from unittest.mock import patch
from stroy.worker.executors import ComfyUIExecutor
from stroy.services.adapters import ComfyUIAdapter
from stroy.worker.main import parse_capabilities


class StubComfyAdapter:
    def __init__(self, fail_free=False, version="0.35.0"):
        self.calls: list[str] = []
        self.wait_timeout: int | None = None
        self.fail_free = fail_free
        self._version = version

    async def free_memory(self):
        self.calls.append("free")
        if self.fail_free:
            raise httpx.HTTPError("boom")

    async def submit(self, graph, client_id):
        self.calls.append("submit")
        return "prompt-1"

    async def wait(self, prompt_id, *, poll_seconds=1.0, timeout_seconds=900):
        self.calls.append("wait")
        self.wait_timeout = timeout_seconds
        return {"prompt_id": prompt_id, "status": {"status": "success"}, "outputs": {}}

    async def collect_output_images(self, history):
        self.calls.append("collect")
        return [{"semantic_name": "image", "filename": "render.png", "media_type": "image/png", "data": b"x"}]

    async def server_version(self):
        return self._version

    async def cancel(self, prompt_id):
        self.calls.append("cancel")
        return True

def get_valid_job_payload():
    return {
        "job_id": "job-1",
        "payload": {
            "workflow_manifest": {
                "id": "flux-redesign-v0",
                "version": "0.2.0",
                "model_profile": "flux-dev-family",
                "required_inputs": ["prompt", "seed"],
                "outputs": ["image"],
                "bindings": {
                    "prompt": {"node_id": "2", "input_name": "text"},
                    "seed": {"node_id": "6", "input_name": "seed"},
                },
                "graph": {
                    "2": {"inputs": {}},
                    "6": {"inputs": {}},
                },
            },
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
        }
    }

@pytest.mark.asyncio
async def test_comfy_vram_free_before_submit():
    adapter = StubComfyAdapter()
    with patch.dict(os.environ, {"STROY_COMFY_FREE_BEFORE": "1"}):
        executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family")
        await executor.execute(get_valid_job_payload())
        assert adapter.calls[:2] == ["free", "submit"]

@pytest.mark.asyncio
async def test_comfy_vram_free_disabled():
    adapter = StubComfyAdapter()
    with patch.dict(os.environ, {"STROY_COMFY_FREE_BEFORE": "0"}):
        executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family")
        await executor.execute(get_valid_job_payload())
        assert "free" not in adapter.calls
        assert "submit" in adapter.calls

@pytest.mark.asyncio
async def test_comfy_vram_free_error_does_not_fail_job():
    adapter = StubComfyAdapter(fail_free=True)
    with patch.dict(os.environ, {"STROY_COMFY_FREE_BEFORE": "1"}):
        executor = ComfyUIExecutor(adapter, "worker-1", "flux-dev-family")
        # Should not raise
        await executor.execute(get_valid_job_payload())
        assert "submit" in adapter.calls

@pytest.mark.asyncio
async def test_comfy_timeout_fallback():
    # Test env fallback
    adapter_env = StubComfyAdapter()
    with patch.dict(os.environ, {"STROY_COMFY_TIMEOUT_SECONDS": "1234"}):
        executor = ComfyUIExecutor(adapter_env, "worker-1", "flux-dev-family")
        await executor.execute(get_valid_job_payload())
        assert adapter_env.wait_timeout == 1234

    # Test payload override
    adapter_payload = StubComfyAdapter()
    job = get_valid_job_payload()
    job["payload"]["timeout_seconds"] = 500
    with patch.dict(os.environ, {"STROY_COMFY_TIMEOUT_SECONDS": "1234"}):
        executor = ComfyUIExecutor(adapter_payload, "worker-1", "flux-dev-family")
        await executor.execute(job)
        assert adapter_payload.wait_timeout == 500

@pytest.mark.asyncio
async def test_comfy_provenance():
    # Versioned
    adapter_v = StubComfyAdapter(version="0.35.0")
    executor_v = ComfyUIExecutor(adapter_v, "worker-1", "flux-dev-family")
    result_v = await executor_v.execute(get_valid_job_payload())
    assert result_v["adapter_provenance"] == {"adapter": "comfyui", "server_version": "0.35.0"}

    # Unversioned
    adapter_none = StubComfyAdapter(version=None)
    executor_none = ComfyUIExecutor(adapter_none, "worker-1", "flux-dev-family")
    result_none = await executor_none.execute(get_valid_job_payload())
    assert result_none["adapter_provenance"] == {"adapter": "comfyui"}

def test_capability_parsing():
    # Default
    assert parse_capabilities(None) == ["llm", "style_analysis", "image_generation", "image_edit", "blender_render", "geometry_quality"]
    
    # Custom valid
    assert parse_capabilities("style_analysis, image_generation") == ["style_analysis", "image_generation"]
    
    # Invalid token
    with pytest.raises(SystemExit):
        parse_capabilities("llm, unknown_cap")

@pytest.mark.asyncio
async def test_adapter_cancel_behavior():
    adapter = ComfyUIAdapter("http://comfy")
    
    with patch("stroy.services.adapters._request_json") as mock_req:
        mock_req.return_value = {"cancelled": "not-a-bool"}
        assert await adapter.cancel("p1") is False
        
        mock_req.side_effect = httpx.HTTPError("fail")
        assert await adapter.cancel("p1") is False

@pytest.mark.asyncio
async def test_adapter_server_version():
    adapter = ComfyUIAdapter("http://comfy")
    
    with patch("stroy.services.adapters._request_json") as mock_req:
        # Happy path
        mock_req.return_value = {"system": {"comfyui_version": "0.3.5"}}
        assert await adapter.server_version() == "0.3.5"
        
        # Caching
        mock_req.return_value = {"system": {"comfyui_version": "0.4.0"}}
        assert await adapter.server_version() == "0.3.5"
        
        # Error path
        adapter._server_version = None # reset cache
        mock_req.side_effect = httpx.HTTPError("fail")
        assert await adapter.server_version() is None
