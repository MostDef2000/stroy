import json
from pathlib import Path

import jsonschema

from stroy.generation import GenerationContext, finalize_generation_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_finalized_generation_manifest_matches_versioned_schema() -> None:
    context = GenerationContext(
        generation_id="generation-1",
        scene_revision_id="scene-rev-1",
        design_revision_id="design-rev-1",
        camera_id="camera.living.entry",
        seed=42,
        input_asset_ids=["asset-depth", "asset-reference"],
        structured_conditioning={"prompt": "warm minimal"},
    )
    manifest = finalize_generation_manifest(
        context=context,
        workflow_id="flux-redesign",
        workflow_version="0.1.0",
        model_profile="flux1-schnell",
        output_asset_ids=["asset-output"],
    )
    payload = manifest.model_dump(mode="json", exclude_none=True)
    schema = json.loads(
        (ROOT / "schemas" / "generation-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(payload, schema)
    assert payload["workflow"] == {
        "id": "flux-redesign",
        "version": "0.1.0",
    }
    assert payload["output_asset_ids"] == ["asset-output"]
