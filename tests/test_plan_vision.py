from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from stroy.domain.plan import PlanDraft
from stroy.plan.qwen_plan import (
    MockPlanAdapter,
    QwenPlanAdapter,
    normalize_plan_draft,
)
from stroy.services.adapters import AdapterProtocolError
from stroy.services.plans import build_scene_from_draft
from stroy.worker.executors import (
    PlanAnalyzeExecutor,
    build_plan_analyze_executor,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _raw_draft(scale: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "units": "mm",
        "scale": scale or {"source": "plan_label", "mm_per_px": 10.0},
        "floors": [
            {
                "name": "main",
                "level_mm": 0,
                "walls": [
                    {
                        "id": "wall.1",
                        "x1": 0,
                        "y1": 0,
                        "x2": 100,
                        "y2": 0,
                        "thickness_mm": 5,
                        "openings": [
                            {
                                "id": "opening.1",
                                "kind": "door",
                                "t": 0.5,
                                "width_mm": 9,
                                "height_mm": 21,
                                "sill_mm": 0,
                            }
                        ],
                    },
                    {"id": "wall.2", "x1": 100, "y1": 0, "x2": 100, "y2": 80,
                     "thickness_mm": 5, "openings": []},
                    {"id": "wall.3", "x1": 100, "y1": 80, "x2": 0, "y2": 80,
                     "thickness_mm": 5, "openings": []},
                    {"id": "wall.4", "x1": 0, "y1": 80, "x2": 0, "y2": 0,
                     "thickness_mm": 5, "openings": []},
                ],
                "rooms": [
                    {
                        "id": "room.1",
                        "name": "Room 1",
                        "wall_ids": ["wall.1", "wall.2", "wall.3", "wall.4"],
                        "floor_finish": None,
                    }
                ],
            }
        ],
    }


class StubLLM:
    def __init__(self, replies: list[str], model: str = "qwen3-vl:8b") -> None:
        self.replies = list(replies)
        self.model = model
        self.profile_id: str | None = "qwen3-vl-8b"
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.replies.pop(0)}}]}


def _adapter(replies: list[str]) -> tuple[QwenPlanAdapter, StubLLM]:
    stub = StubLLM(replies)
    return QwenPlanAdapter(stub, attempts=2), stub


async def test_strict_json_happy_path_normalizes_to_mm() -> None:
    adapter, stub = _adapter([json.dumps(_raw_draft())])
    result = await adapter.analyze_plan(images=[_PNG])
    draft = result["plan_draft"]
    assert draft["scale"] == {"source": "plan_label", "mm_per_px": 10.0}
    wall = draft["floors"][0]["walls"][0]
    assert wall["x2"] == 1000.0
    assert wall["thickness_mm"] == 50.0
    assert wall["openings"][0]["width_mm"] == 90.0
    assert result["adapter_provenance"]["adapter"] == "qwen-plan"
    assert len(stub.calls) == 1
    user_content = stub.calls[0][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_invalid_reply_retries_then_succeeds() -> None:
    adapter, stub = _adapter(["this is not json", json.dumps(_raw_draft())])
    result = await adapter.analyze_plan(images=[_PNG])
    assert result["plan_draft"]["scale"]["source"] == "plan_label"
    assert len(stub.calls) == 2
    assert "rejected" in stub.calls[1][1]["content"][0]["text"]


async def test_persistently_invalid_reply_fails_after_one_retry() -> None:
    adapter, stub = _adapter(["nope", "still nope"])
    with pytest.raises(AdapterProtocolError, match="did not return a valid plan draft"):
        await adapter.analyze_plan(images=[_PNG])
    assert len(stub.calls) == 2


async def test_unknown_scale_keeps_pixel_coordinates() -> None:
    adapter, _ = _adapter(
        [json.dumps(_raw_draft(scale={"source": "unknown", "mm_per_px": None}))]
    )
    draft = (await adapter.analyze_plan(images=[_PNG]))["plan_draft"]
    assert draft["scale"] == {"source": "unknown"}
    assert draft["floors"][0]["walls"][0]["x2"] == 100.0


async def test_manual_hint_derives_scale_and_normalizes() -> None:
    adapter, _ = _adapter(
        [json.dumps(_raw_draft(scale={"source": "unknown", "mm_per_px": None}))]
    )
    result = await adapter.analyze_plan(
        images=[_PNG],
        hints={"known_wall_length_mm": 5000, "length_mm": 250, "wall_asset_index": 0},
    )
    draft = result["plan_draft"]
    assert draft["scale"] == {"source": "manual", "mm_per_px": 20.0}
    assert draft["floors"][0]["walls"][0]["x2"] == 2000.0


def test_normalize_does_not_mutate_caller() -> None:
    raw = _raw_draft()
    normalize_plan_draft(raw)
    assert raw["floors"][0]["walls"][0]["x2"] == 100


async def test_mock_plan_adapter_is_deterministic() -> None:
    adapter = MockPlanAdapter()
    first = await adapter.analyze_plan(images=[_PNG])
    second = await adapter.analyze_plan(images=[_PNG])
    assert first == second
    assert first["adapter_provenance"]["adapter"] == "mock-plan"
    assert first["plan_draft"]["floors"][0]["rooms"][0]["wall_ids"]


class RecordingPlanAdapter:
    def __init__(self) -> None:
        self.seen: list[list[bytes]] = []

    def provenance(self) -> dict[str, Any]:
        return {"adapter": "recording", "model_profile": "test"}

    async def analyze_plan(
        self,
        *,
        images: list[bytes],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.seen.append(images)
        return {
            "plan_draft": _raw_draft(),
            "adapter_provenance": self.provenance(),
        }


async def test_executor_single_image_stays_byte_identical() -> None:
    adapter = RecordingPlanAdapter()
    executor = PlanAnalyzeExecutor(adapter)  # type: ignore[arg-type]
    result = await executor.execute({"payload": {"images": [_PNG]}})
    assert result["plan_draft"] == _raw_draft()


async def test_executor_merges_multi_image_drafts_with_reid() -> None:
    adapter = RecordingPlanAdapter()
    executor = PlanAnalyzeExecutor(adapter)  # type: ignore[arg-type]
    result = await executor.execute({"payload": {"images": [_PNG, _PNG]}})
    assert adapter.seen == [[_PNG], [_PNG]]

    draft = result["plan_draft"]
    # Same floor name from both images collapses into ONE floor.
    assert len(draft["floors"]) == 1
    floor = draft["floors"][0]
    wall_ids = [wall["id"] for wall in floor["walls"]]
    opening_ids = [
        opening["id"] for wall in floor["walls"] for opening in wall["openings"]
    ]
    room_ids = [room["id"] for room in floor["rooms"]]
    all_ids = wall_ids + opening_ids + room_ids
    assert len(all_ids) == len(set(all_ids))
    assert len(wall_ids) == 8  # 4 walls per image x 2 images
    assert set(wall_ids) == {
        "wall.1.img0",
        "wall.2.img0",
        "wall.3.img0",
        "wall.4.img0",
        "wall.1.img1",
        "wall.2.img1",
        "wall.3.img1",
        "wall.4.img1",
    }
    # room.wall_ids are remapped onto the re-id'd walls.
    known = set(wall_ids)
    for room in floor["rooms"]:
        assert len(room["wall_ids"]) == 4
        assert set(room["wall_ids"]).issubset(known)

    # Commit path: the merged draft builds a scene without duplicate ids.
    scene = build_scene_from_draft("project.merged", PlanDraft.model_validate(draft))
    scene_ids = [entity.id for entity in scene.entities]
    assert len(scene_ids) == len(set(scene_ids))
    assert len(scene_ids) > 0


async def test_executor_wired_with_fake_multimodal_adapter() -> None:
    stub = StubLLM([json.dumps(_raw_draft())])
    executor = PlanAnalyzeExecutor(QwenPlanAdapter(stub))
    result = await executor.execute({"payload": {"images": [_PNG]}})
    assert result["plan_draft"]["scale"]["mm_per_px"] == 10.0


async def test_executor_without_images_raises() -> None:
    executor = PlanAnalyzeExecutor(RecordingPlanAdapter())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires plan image inputs"):
        await executor.execute({"payload": {}})


def test_build_plan_analyze_executor_wiring() -> None:
    assert isinstance(build_plan_analyze_executor("mock"), PlanAnalyzeExecutor)
    assert isinstance(build_plan_analyze_executor("fake"), PlanAnalyzeExecutor)
    with pytest.raises(ValueError, match="unknown plan analyze adapter"):
        build_plan_analyze_executor("warp-drive")


def _big_png(width: int = 3200, height: int = 2400) -> bytes:
    from io import BytesIO

    from PIL import Image

    # Gaussian noise keeps the PNG far above the 1.5MB downscale threshold
    # (a flat-color plan drawing would compress below it and skip the guard).
    img = Image.effect_noise((width, height), 40).convert("RGB")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _decode_data_url(url: str) -> tuple[str, bytes]:
    header, encoded = url.split(",", 1)
    return header, base64.b64decode(encoded)


def test_data_url_downscales_large_plan_images() -> None:
    from io import BytesIO

    from PIL import Image

    from stroy.plan.qwen_plan import _data_url

    header, payload = _decode_data_url(_data_url(_big_png()))
    assert header == "data:image/jpeg;base64"
    with Image.open(BytesIO(payload)) as img:
        assert max(img.size) <= 2000


def test_data_url_keeps_small_images_untouched() -> None:
    from stroy.plan.qwen_plan import _data_url

    image = _big_png(400, 300)
    header, payload = _decode_data_url(_data_url(image))
    assert header == "data:image/png;base64"
    assert payload == image


def test_downscaled_jpeg_shrinks_payload_far_below_original() -> None:
    from stroy.plan.qwen_plan import _data_url

    original = _big_png()
    _, payload = _decode_data_url(_data_url(original))
    assert len(payload) < len(original) // 5
