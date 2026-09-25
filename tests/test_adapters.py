import httpx
import pytest

from stroy.services.adapters import ComfyUIAdapter, FakeLLMAdapter, OpenAICompatibleLLM


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
