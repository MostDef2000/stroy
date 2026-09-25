# Generative Workflows

Versioned image-generation workflows live here only after they have been validated against the target ComfyUI/runtime version.

## Stable STROY contract

Product/API code works with semantic inputs such as:

- `positive_prompt`
- `seed`
- control images/assets
- style/reference inputs

A STROY `WorkflowManifest` owns the mapping from those names to concrete ComfyUI node IDs and input fields. Node IDs are therefore confined to the generation/workflow adapter layer.

The manifest contract is defined in:

- `schemas/workflow-manifest.schema.json`
- `src/stroy/generation/workflows.py`

Each manifest records a stable workflow ID, version, approved model profile, required semantic inputs, semantic outputs, bindings and the concrete graph.

## Production workflow rule

Do not commit a graph here merely because it looks structurally valid. A production workflow should be exported from and smoke-tested against the intended ComfyUI version/model installation, then pinned as a versioned manifest.

The current test-only example is intentionally kept under `tests/fixtures/workflow-manifest.json`; it is not a runnable FLUX workflow.


## Manifest contract

Each `*.manifest.json` is validated against `schemas/workflow-manifest.schema.json`.

The control plane/job payload supplies:

- `workflow_manifest`: the STROY-owned manifest;
- `workflow_inputs`: semantic values such as `prompt`, `seed`, `depth_asset_id` or `mask_asset_id`.

Only the home-worker generation adapter resolves semantic bindings to concrete ComfyUI node IDs. API/domain code must never construct or inspect those node IDs.

The manifest records `id`, `version`, `model_profile`, required semantic inputs and declared outputs. The executor returns those identifiers in provenance so a later ComfyUI graph change can coexist as a new manifest version.
