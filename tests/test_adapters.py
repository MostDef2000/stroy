import httpx
import pytest

from stroy.services.adapters import (
    AdapterProtocolError,
    AdapterTimeout,
    AdapterUnavailable,
    ComfyUIAdapter,
    FakeLLMAdapter,
    OpenAICompatibleLLM,
)
from stroy.worker.executors import QwenExecutor


@pytest.mark.asyncio
async def test_openai_compatible_adapter_normalizes_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer local"
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleLLM(
        "http://qwen/v1",
        "local",
        "Qwen/Qwen3-14B",
        client=client,
    )
    result = await adapter.complete([{"role": "user", "content": "hello"}])
    assert result["choices"][0]["message"]["content"] == "ok"
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_submit_contract():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/prompt"
        return httpx.Response(200, json={"prompt_id": "prompt-1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ComfyUIAdapter("http://comfy", client=client)
    assert await adapter.submit({"1": {}}, "worker") == "prompt-1"
    await client.aclose()


@pytest.mark.asyncio
async def test_fake_llm_produces_typed_intent():
    result = await FakeLLMAdapter().complete(
        [{"role": "user", "content": "Сделай диван бежевым и убери стол"}]
    )
    assert [call["name"] for call in result["tool_calls"]] == [
        "set_color",
        "remove_object",
    ]


@pytest.mark.asyncio
async def test_qwen_executor_normalizes_openai_tool_calls():
    class RawAdapter:
        def provenance(self):
            return {
                "adapter": "raw-test",
                "model_profile": "qwen-test",
                "model": "test/model",
            }

        async def complete(self, messages, *, tools=None):
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "set_color",
                                        "arguments": (
                                            '{"target_id":"object.sofa.main",'
                                            '"color":"#D7C4AB"}'
                                        ),
                                    }
                                }
                            ],
                        }
                    }
                ]
            }

    result = await QwenExecutor(RawAdapter()).execute(
        {
            "payload": {
                "messages": [{"role": "user", "content": "make sofa beige"}],
                "tools": [],
            }
        }
    )
    assert result["content"] is None
    assert result["adapter_provenance"]["model_profile"] == "qwen-test"
    assert result["tool_calls"] == [
            {
                "name": "set_color",
                "arguments": {
                    "target_id": "object.sofa.main",
                    "color": "#D7C4AB",
                },
            }
        ]


@pytest.mark.asyncio
async def test_openai_adapter_maps_timeout():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleLLM("http://qwen/v1", "local", "model", client=client)
    with pytest.raises(AdapterTimeout):
        await adapter.complete([{"role": "user", "content": "hello"}])
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_adapter_maps_server_unavailable():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "busy"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleLLM("http://qwen/v1", "local", "model", client=client)
    with pytest.raises(AdapterUnavailable):
        await adapter.complete([{"role": "user", "content": "hello"}])
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_missing_prompt_id_is_protocol_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ComfyUIAdapter("http://comfy", client=client)
    with pytest.raises(AdapterProtocolError):
        await adapter.submit({}, "worker")
    await client.aclose()


@pytest.mark.asyncio
async def test_comfyui_wait_returns_completed_history():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path == "/history/prompt-1"
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={"prompt-1": {"outputs": {"node": {"images": []}}}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ComfyUIAdapter("http://comfy", client=client)
    result = await adapter.wait("prompt-1", poll_seconds=0, timeout_seconds=1)
    assert "outputs" in result
    assert calls == 2
    await client.aclose()
