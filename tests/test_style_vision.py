from io import BytesIO
import json
from pathlib import Path

import httpx
from PIL import Image
import pytest

from stroy.services.adapters import OpenAICompatibleLLM
from stroy.style import LocalPixelStyleAdapter, OpenAIVisionStyleAdapter, StyleImage
from stroy.worker.executors import VisionStyleExecutor


ROOT = Path(__file__).resolve().parents[1]


def fixture() -> dict:
    return json.loads(
        (ROOT / "fixtures" / "style-references.json").read_text(encoding="utf-8")
    )


def image_bytes(rgb: list[int], size: list[int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", tuple(size), tuple(rgb)).save(buffer, format="PNG")
    return buffer.getvalue()


def style_images() -> list[StyleImage]:
    data = fixture()
    return [
        StyleImage(
            asset_id=item["asset_id"],
            media_type="image/png",
            data=image_bytes(item["rgb"], item["size"]),
        )
        for item in data["references"]
    ]


@pytest.mark.asyncio
async def test_local_pixel_adapter_reads_fixed_reference_images() -> None:
    data = fixture()
    proposal = await LocalPixelStyleAdapter().analyze(
        data["source_text"],
        style_images(),
    )
    assert proposal.labels == data["expected"]["labels"]
    assert proposal.materials[0].name == data["expected"]["material"]
    assert proposal.lighting.temperature_k == data["expected"]["temperature_k"]
    assert len(proposal.palette) >= 3
    assert {
        item["asset_id"]
        for item in proposal.evidence["images"]
    } == {"ref-1", "ref-2", "ref-3"}


@pytest.mark.asyncio
async def test_openai_vision_adapter_sends_inline_image_content() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.update(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "labels": ["minimal"],
                                    "palette": [{"hex": "#E8E0D6", "role": "base"}],
                                    "materials": [{"name": "wood"}],
                                    "lighting": {"temperature_k": 3000, "intent": ["soft"]},
                                    "forms": {"keywords": ["clean lines"]},
                                    "negative_constraints": [],
                                    "evidence": {"runtime": "fixture"},
                                }
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OpenAICompatibleLLM(
        "http://vision/v1",
        "local",
        "vision-model",
        profile_id="vision-profile",
        client=client,
    )
    proposal = await OpenAIVisionStyleAdapter(llm).analyze(
        "minimal",
        style_images(),
    )
    assert proposal.labels == ["minimal"]
    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "text"
    image_parts = [item for item in content if item["type"] == "image_url"]
    assert len(image_parts) == 3
    assert all(
        item["image_url"]["url"].startswith("data:image/png;base64,")
        for item in image_parts
    )
    await client.aclose()


class FakeWorkerClient:
    def __init__(self) -> None:
        self.images = {
            item.asset_id: item.data
            for item in style_images()
        }

    async def download_input(self, url: str) -> bytes:
        return self.images[url.removeprefix("asset://")]


@pytest.mark.asyncio
async def test_vision_style_executor_downloads_all_reference_assets() -> None:
    executor = VisionStyleExecutor(FakeWorkerClient(), LocalPixelStyleAdapter())
    result = await executor.execute(
        {
            "download_urls": {
                "ref-1": "asset://ref-1",
                "ref-2": "asset://ref-2",
                "ref-3": "asset://ref-3",
            },
            "payload": {
                "source_text": fixture()["source_text"],
                "input_assets": [
                    {"id": "ref-1", "media_type": "image/png"},
                    {"id": "ref-2", "media_type": "image/png"},
                    {"id": "ref-3", "media_type": "image/png"},
                ],
            },
        }
    )
    assert result["style_profile"]["labels"] == ["minimal"]
    assert result["adapter_provenance"]["adapter"] == "local-pixel-style-v0"
