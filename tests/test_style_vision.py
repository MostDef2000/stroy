import json
import pytest
from pathlib import Path
from jsonschema import validate

from stroy.style.models import (
    PaletteEntry,
    StyleProfile,
    StyleProfileProposal,
    StyleOverrides,
    merge_style_overrides,
)
from stroy.style.vision import MockVisionStyleAdapter
from stroy.worker.executors import (
    VisionStyleExecutor,
    FakeStyleExecutor,
    build_style_analyze_executor,
)

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def mock_vision_adapter():
    return MockVisionStyleAdapter()

@pytest.fixture
def vision_executor(mock_vision_adapter):
    return VisionStyleExecutor(mock_vision_adapter)

@pytest.fixture
def schema_data():
    with open(ROOT / "schemas/style-profile.schema.json", "r") as f:
        return json.load(f)

@pytest.fixture
def img_bytes_1():
    return b"fake-image-1-content-fixed"

@pytest.mark.asyncio
async def test_vision_adapter_deterministic(mock_vision_adapter, img_bytes_1):
    """Same inputs must yield identical outputs."""
    text = "Modern minimal living room"
    res1 = await mock_vision_adapter.analyze_style(images=[img_bytes_1], source_text=text)
    res2 = await mock_vision_adapter.analyze_style(images=[img_bytes_1], source_text=text)
    assert res1 == res2
    assert res1["adapter_provenance"]["adapter"] == "mock-vision"

@pytest.mark.asyncio
async def test_vision_adapter_palette_normalization(mock_vision_adapter, img_bytes_1):
    """Palette hex values MUST be normalized uppercase."""
    res = await mock_vision_adapter.analyze_style(images=[img_bytes_1])
    palette = res["style_profile"]["palette"]
    for entry in palette:
        hex_val = entry["hex"]
        assert hex_val == hex_val.upper()
        assert hex_val.startswith("#")
        assert len(hex_val) == 7

@pytest.mark.asyncio
async def test_vision_adapter_schema_validation(mock_vision_adapter, img_bytes_1, schema_data):
    """Output style_profile must validate against the JSON schema when converted to StyleProfile."""
    res = await mock_vision_adapter.analyze_style(images=[img_bytes_1])
    proposal_dict = res["style_profile"]

    # Build a full StyleProfile from the proposal fields
    profile = StyleProfile(
        style_profile_id="test-id",
        source_asset_ids=["asset-1"],
        source_text="test text",
        labels=proposal_dict["labels"],
        palette=[PaletteEntry(**p) for p in proposal_dict["palette"]],
        materials=[p for p in proposal_dict["materials"]], # Simplified for mock
        lighting=proposal_dict["lighting"],
        forms=proposal_dict["forms"],
        negative_constraints=proposal_dict["negative_constraints"],
    )

    validate(instance=profile.model_dump(mode="json", exclude_none=True), schema=schema_data)

@pytest.mark.asyncio
async def test_vision_style_overrides(mock_vision_adapter, img_bytes_1):
    """Source text overrides applied deterministically via merge_style_overrides."""
    res = await mock_vision_adapter.analyze_style(images=[img_bytes_1])
    proposal = StyleProfileProposal.model_validate(res["style_profile"])

    overrides = StyleOverrides(
        labels=["overridden-label"],
        palette=[PaletteEntry(hex="#FF0000", role="override")]
    )

    merged = merge_style_overrides(proposal, overrides)
    assert merged.labels == ["overridden-label"]
    assert merged.palette[0].hex == "#FF0000"
    assert merged.lighting == proposal.lighting

@pytest.mark.asyncio
async def test_vision_executor_pass_through(mock_vision_adapter, img_bytes_1):
    """Executor handles data and returns adapter result as-is."""
    executor = VisionStyleExecutor(mock_vision_adapter)
    job = {
        "payload": {
            "images": [img_bytes_1],
            "source_text": "Test",
            "input_asset_ids": ["asset-1"]
        }
    }
    res = await executor.execute(job)
    assert "style_profile" in res
    assert res["adapter_provenance"]["adapter"] == "mock-vision"

@pytest.mark.asyncio
async def test_vision_executor_fallback_bytes(mock_vision_adapter):
    """Jobs without payload.images derive deterministic bytes from asset ids / job id."""
    executor = VisionStyleExecutor(mock_vision_adapter)

    by_assets = await executor.execute(
        {
            "job_id": "job-1",
            "payload": {"source_text": "x", "input_asset_ids": ["asset-1", "asset-2"]},
        }
    )
    by_assets_again = await executor.execute(
        {
            "job_id": "job-1",
            "payload": {"source_text": "x", "input_asset_ids": ["asset-1", "asset-2"]},
        }
    )
    assert by_assets == by_assets_again
    assert "style_profile" in by_assets

    by_job_id = await executor.execute({"job_id": "job-9", "payload": {"source_text": "x"}})
    by_job_id_again = await executor.execute({"job_id": "job-9", "payload": {"source_text": "x"}})
    assert by_job_id == by_job_id_again
    assert "style_profile" in by_job_id

@pytest.mark.asyncio
async def test_vision_adapter_invalid_input(mock_vision_adapter):
    """Raise error if no images are provided."""
    from stroy.services.adapters import AdapterProtocolError
    with pytest.raises(AdapterProtocolError, match="requires at least one image"):
        await mock_vision_adapter.analyze_style(images=[])

def test_executor_wiring():
    """Verify factory wiring."""
    assert isinstance(build_style_analyze_executor("mock"), VisionStyleExecutor)
    assert isinstance(build_style_analyze_executor("fake"), FakeStyleExecutor)
    with pytest.raises(ValueError, match="unknown style vision adapter"):
        build_style_analyze_executor("unknown")
