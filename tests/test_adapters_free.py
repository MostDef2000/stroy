import pytest
import httpx
from stroy.services.adapters import ComfyUIAdapter, AdapterTimeout, AdapterUnavailable, AdapterProtocolError
from stroy.worker.executors import ComfyUIExecutor

class StubComfyAdapter(ComfyUIAdapter):
    def __init__(self, fail_free_kind=None):
        self.fail_free_kind = fail_free_kind
        self.submit_called = False

    async def free_memory(self) -> None:
        if self.fail_free_kind == "timeout":
            raise AdapterTimeout("timeout")
        if self.fail_free_kind == "unavailable":
            raise AdapterUnavailable("unavailable")
        if self.fail_free_kind == "protocol":
            raise AdapterProtocolError("protocol")
        return None

    async def submit(self, workflow, client_id) -> str:
        self.submit_called = True
        return "prompt-123"

    async def wait(self, prompt_id, **kwargs):
        return {"outputs": {}}

    async def collect_output_images(self, history):
        return []

    async def server_version(self):
        return "1.0.0"

@pytest.mark.asyncio
async def test_free_memory_empty_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/free"
        return responses.pop(0)

    responses = [httpx.Response(200, content=b"")]
    adapter = ComfyUIAdapter(
        "http://comfy", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    # real ComfyUI answers 200 with an EMPTY body - must not raise
    await adapter.free_memory()

    responses.append(httpx.Response(200, json={"ok": True}))
    await adapter.free_memory()  # JSON body is also fine

    responses.append(httpx.Response(500, text="boom"))
    with pytest.raises(AdapterUnavailable):
        await adapter.free_memory()

    responses.append(httpx.Response(404, text="nope"))
    with pytest.raises(AdapterProtocolError):
        await adapter.free_memory()

@pytest.mark.asyncio
async def test_executor_free_memory_degrades():
    # Test that AdapterTimeout/AdapterUnavailable during free_memory doesn't fail the job
    for fail_kind in ["timeout", "unavailable", "protocol"]:
        adapter = StubComfyAdapter(fail_free_kind=fail_kind)
        executor = ComfyUIExecutor(adapter, "worker-1", "profile-1")
        job = {
            "payload": {
                "workflow_manifest": {
                    "id": "wf-1",
                    "version": "1",
                    "model_profile": "profile-1",
                    "outputs": ["image"],
                    "graph": {"1": {"class_type": "SaveImage", "inputs": {}}},
                },
                "inputs": {},
                "generation": {
                    "generation_id": "gen-1",
                    "scene_revision_id": "scene-1",
                    "design_revision_id": "design-1",
                    "camera_id": "default",
                },
            },
            "job_id": "job-1"
        }
        # Should not raise exception
        await executor.execute(job)
        assert adapter.submit_called
