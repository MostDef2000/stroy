from stroy.services.generations import ensure_generation_payload

def test_ensure_generation_payload_bare_payload():
    payload = {"scene_revision_id": "x"}
    result = ensure_generation_payload(payload)
    assert result["inputs"]["prompt"] == "Test generation"
    assert result["inputs"]["seed"] == 0
    assert "generation" in result
    assert "workflow_manifest" in result

def test_ensure_generation_payload_with_custom_prompt():
    payload = {"prompt": "custom prompt", "scene_revision_id": "x"}
    result = ensure_generation_payload(payload)
    assert result["inputs"]["prompt"] == "custom prompt"
    assert result["inputs"]["seed"] == 0

def test_ensure_generation_payload_with_inputs():
    payload = {
        "inputs": {"prompt": "custom prompt"},
        "scene_revision_id": "x"
    }
    result = ensure_generation_payload(payload)
    assert result["inputs"]["prompt"] == "custom prompt"
    assert result["inputs"]["seed"] == 0

def test_ensure_generation_payload_early_return():
    # Queue-style payload has 'generation' already
    payload = {
        "generation": {"generation_id": "gen-123"},
        "inputs": {"prompt": "should stay", "seed": 42},
        "workflow_manifest": {"id": "wf-1"}
    }
    result = ensure_generation_payload(payload)
    assert result == payload
    assert result["inputs"]["prompt"] == "should stay"
    assert result["inputs"]["seed"] == 42
